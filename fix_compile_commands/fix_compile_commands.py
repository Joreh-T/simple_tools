#!/usr/bin/env python3
"""
A script to fix incorrect paths in a compile_commands.json file.

This script processes a compile_commands.json file, identifies paths that
point to a build output directory (e.g., 'output/'), and attempts to find
the corresponding real source file paths in one or more provided source
code directories.

It uses 'fd' to search for files and employs a path similarity scoring
algorithm to disambiguate between multiple potential matches.
"""

import json
import sys
import os
import subprocess
import re
from typing import List, Dict, Optional, Any

# --- Constants ---

# Directories to exclude from file searches.
EXCLUDED_DIRS = [".cache", "output"]

# Minimum similarity score for a path match to be considered valid.
# This helps prevent incorrect matches when the real source file is missing.
MIN_SCORE_THRESHOLD = 1.5

# --- Path Resolution Logic ---

# A cache to store the results of path lookups to speed up processing.
PATH_CACHE: Dict[str, Optional[str]] = {}

def _simple_string_similarity(a: str, b: str) -> float:
    """Calculates a simple Jaccard similarity score between two strings."""
    set_a = set(a)
    set_b = set(b)
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0

def _get_best_match(matches: List[str], original_path: str) -> Optional[str]:
    """
    Scores and selects the best match from a list of candidates based on
    path similarity to the original path.
    """
    if not matches:
        return None

    original_suffix_parts: List[str] = []
    if 'output/build/' in original_path:
        original_suffix = original_path.split('output/build/', 1)[1]
        original_suffix_parts = original_suffix.split(os.sep)
    else:
        # Fallback for unexpected path structures.
        original_suffix_parts = original_path.split(os.sep)[-4:]

    best_score = -1.0
    best_match_path = None

    for match_path in matches:
        match_parts = match_path.split(os.sep)
        
        score = 0.0
        # Compare path components from right to left for similarity.
        for i in range(1, min(len(original_suffix_parts), len(match_parts)) + 1):
            original_part = original_suffix_parts[-i]
            match_part = match_parts[-i]
            
            if original_part == match_part:
                score += 1.0
            else:
                # If parts don't match exactly, add a partial score based on
                # string similarity and then stop comparing further up the tree.
                similarity = _simple_string_similarity(original_part, match_part)
                score += similarity
                break
        
        if score > best_score:
            best_score = score
            best_match_path = match_path

    if best_score < MIN_SCORE_THRESHOLD:
        return None

    return os.path.abspath(best_match_path) if best_match_path else None

def _run_fd_search(
    search_term: str,
    source_roots: List[str],
    is_regex: bool = False
) -> List[str]:
    """Executes the 'fd' command with a given search term and returns matches."""
    cmd = ["fd", "-HI"]
    if is_regex:
        cmd.extend(["--regex", search_term])
    else:
        cmd.append(search_term)
    
    for root in source_roots:
        cmd.append(root)

    for excluded in EXCLUDED_DIRS:
        cmd.extend(["--exclude", excluded])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
        return result.stdout.strip().splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

def find_real_path(path: str, source_roots: List[str]) -> Optional[str]:
    """
    Finds the real source path for a given path from the build output.
    Returns the absolute path of the best match, or None if no suitable
    match is found.
    """
    if path in PATH_CACHE:
        return PATH_CACHE[path]

    if "output/" not in path:
        PATH_CACHE[path] = path
        return path

    # Strategy 1: Search for a more specific partial path (e.g., "parent/file.c").
    p = path.rstrip(os.sep)
    parent = os.path.dirname(p)
    if parent and parent != os.sep:
        search_term = os.path.join(os.path.basename(parent), os.path.basename(p))
        matches = _run_fd_search(search_term, source_roots)
        filtered_matches = [m for m in matches if m.endswith(search_term)]
        best_match = _get_best_match(filtered_matches, path)
        if best_match:
            PATH_CACHE[path] = best_match
            return best_match

    # Strategy 2: Fallback to searching for the exact basename.
    name = os.path.basename(path)
    if name:
        regex_term = f"^{re.escape(name)}$"
        matches = _run_fd_search(regex_term, source_roots, is_regex=True)
        best_match = _get_best_match(matches, path)
        if best_match:
            PATH_CACHE[path] = best_match
            return best_match

    PATH_CACHE[path] = None
    return None

# --- Main Application Logic ---

def process_entry(entry: Dict[str, Any], source_roots: List[str]) -> Dict[str, Any]:
    """
    Processes a single entry from the compilation database, fixing its paths.
    """
    original_directory = entry.get('directory', '')
    original_file = entry.get('file', '')

    if not original_directory or not original_file:
        return entry

    # Find the real path of the source file, which acts as our anchor.
    original_abs_file = original_file if os.path.isabs(original_file) else os.path.join(original_directory, original_file)
    found_abs_file_path = find_real_path(original_abs_file, source_roots)

    if found_abs_file_path is None:
        return entry # If file not found, don't modify the entry.

    # Derive the new directory and file paths from the found absolute file path.
    try:
        rel_path = os.path.relpath(original_abs_file, original_directory)
        rel_path_parts = rel_path.split(os.sep)
        new_directory = os.path.abspath(os.path.join(found_abs_file_path, *(['..'] * len(rel_path_parts))))
        entry['directory'] = new_directory
        entry['file'] = os.path.relpath(found_abs_file_path, new_directory)
    except ValueError:
        # Fallback for complex cases (e.g., different drives on Windows).
        new_directory = os.path.dirname(found_abs_file_path)
        entry['directory'] = new_directory
        entry['file'] = os.path.basename(found_abs_file_path)

    # Process compiler arguments to fix any paths therein.
    new_args = []
    for arg in entry.get('arguments', []):
        if not isinstance(arg, str):
            new_args.append(arg)
            continue

        if 'output/' in arg:
            found_arg = find_real_path(arg, source_roots)
            new_args.append(found_arg if found_arg is not None else arg)
        elif arg.endswith(('.c', '.cpp', '.h', '.S')) and not os.path.isabs(arg):
            abs_path = os.path.join(original_directory, arg)
            found_abs_path = find_real_path(abs_path, source_roots)
            if found_abs_path:
                if new_directory and found_abs_path.startswith(new_directory + os.sep):
                    new_args.append(os.path.relpath(found_abs_path, new_directory))
                else:
                    new_args.append(found_abs_path)
            else:
                new_args.append(arg)
        else:
            new_args.append(arg)
    entry['arguments'] = new_args
    
    return entry

def main():
    """Main function to run the script."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <compile_commands.json> <source_root1> [<source_root2> ...]")
        sys.exit(1)

    cdb_file = sys.argv[1]
    source_roots = [os.path.abspath(d) for d in sys.argv[2:]]

    print(f"Loading JSON from {cdb_file} ...")
    try:
        with open(cdb_file, 'r') as f:
            data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error reading or parsing {cdb_file}: {e}")
        sys.exit(1)

    total_entries = len(data)
    print(f"Total entries to process: {total_entries}")

    processed_data = []
    try:
        for idx, entry in enumerate(data, 1):
            print(f"[{idx}/{total_entries}] Processing: {entry.get('file', '')}")
            print(f"[{idx}/{total_entries}]\r")
            processed_entry = process_entry(entry, source_roots)
            processed_data.append(processed_entry)
    except KeyboardInterrupt:
        print("\nInterrupted by user. Saving partial results...")

    out_file = os.path.splitext(cdb_file)[0] + "_fixed.json"
    print(f"Saving {len(processed_data)} processed entries to {out_file}...")
    try:
        with open(out_file, 'w') as f:
            json.dump(processed_data, f, indent=2)
    except IOError as e:
        print(f"Error writing to {out_file}: {e}")
        sys.exit(1)

    if len(processed_data) < total_entries:
        print("Processing was interrupted. The output file contains partial results.")
    else:
        print("Successfully fixed compile_commands.json.")

if __name__ == "__main__":
    main()
