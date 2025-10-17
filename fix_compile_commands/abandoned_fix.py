#!/usr/bin/env python3
import json
import sys
import os
import subprocess
import re

if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} <compile_commands.json> <source_root1> [<source_root2> ...]")
    sys.exit(1)

cdb_file = sys.argv[1]
source_roots = [os.path.abspath(d) for d in sys.argv[2:]]

# search cache
path_cache = {}

def _simple_string_similarity(a, b):
    set_a = set(a)
    set_b = set(b)
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0

def _get_best_match(matches, original_path):
    if not matches:
        return None

    original_suffix_parts = []
    if 'output/build/' in original_path:
        original_suffix = original_path.split('output/build/', 1)[1]
        original_suffix_parts = original_suffix.split(os.sep)
    else:
        original_suffix_parts = original_path.split(os.sep)[-4:]

    best_score = -1.0
    best_match_path = None

    for match_path in matches:
        match_parts = match_path.split(os.sep)
        
        score = 0.0
        for i in range(1, min(len(original_suffix_parts), len(match_parts)) + 1):
            original_part = original_suffix_parts[-i]
            match_part = match_parts[-i]
            
            if original_part == match_part:
                score += 1.0
            else:
                similarity = _simple_string_similarity(original_part, match_part)
                score += similarity
                break
        
        if score > best_score:
            best_score = score
            best_match_path = match_path

    MIN_SCORE_THRESHOLD = 1.5
    if best_score < MIN_SCORE_THRESHOLD:
        return None

    return os.path.abspath(best_match_path)

def find_real_path(path):
    """ use fd to find the real path of a file or directory, caching results. Return None if not found. """
    if path in path_cache:
        return path_cache[path]

    if "output/" not in path:
        path_cache[path] = path
        return path

    p = path.rstrip(os.sep)
    parent = os.path.dirname(p)
    
    search_term = ""
    if parent and parent != os.sep:
        search_term = os.path.join(os.path.basename(parent), os.path.basename(p))
    else:
        search_term = os.path.basename(p)

    if search_term:
        for base in source_roots:
            try:
                result = subprocess.run(
                    ["fd", "-HI", search_term, base, "--exclude", ".cache", "--exclude", ".cqche", "--exclude", "output"],
                    capture_output=True, text=True, check=True
                )
                lines = result.stdout.strip().splitlines()
                filtered_lines = [line for line in lines if line.endswith(search_term)]
                
                best_match = _get_best_match(filtered_lines, path)
                if best_match:
                    path_cache[path] = best_match
                    return best_match
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass

    name = os.path.basename(path)
    if name:
        regex_name = f"^{re.escape(name)}$"
        for base in source_roots:
            try:
                result = subprocess.run(
                    ["fd", "-HI", "--regex", regex_name, base, "--exclude", ".cache", "--exclude", ".cqche", "--exclude", "output"],
                    capture_output=True, text=True, check=True
                )
                lines = result.stdout.strip().splitlines()
                best_match = _get_best_match(lines, path)
                if best_match:
                    path_cache[path] = best_match
                    return best_match
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass

    path_cache[path] = None
    return None

print(f"Loading JSON from {cdb_file} ...")
with open(cdb_file, 'r') as f:
    data = json.load(f)

total_entries = len(data)
print(f"Total entries to process: {total_entries}")

new_data = []
try:
    for idx, entry in enumerate(data, 1):
        original_directory = entry.get('directory', '')
        original_file = entry.get('file', '')

        if not original_directory or not original_file:
            new_data.append(entry)
            continue

        print(f"[{idx}/{total_entries}] Processing: {original_file}")

        original_abs_file = original_file if os.path.isabs(original_file) else os.path.join(original_directory, original_file)
        found_abs_file_path = find_real_path(original_abs_file)

        if found_abs_file_path is None:
            new_data.append(entry)
            continue

        try:
            rel_path = os.path.relpath(original_abs_file, original_directory)
            rel_path_parts = rel_path.split(os.sep)
            
            new_directory_path = os.path.abspath(os.path.join(found_abs_file_path, *(['..'] * len(rel_path_parts))))
            
            entry['directory'] = new_directory_path
            entry['file'] = os.path.relpath(found_abs_file_path, new_directory_path)
            new_directory = new_directory_path
        except ValueError:
            entry['directory'] = os.path.dirname(found_abs_file_path)
            entry['file'] = os.path.basename(found_abs_file_path)
            new_directory = entry['directory']

        new_args = []
        for arg in entry.get('arguments', []):
            if not isinstance(arg, str):
                new_args.append(arg)
                continue

            if 'output/' in arg:
                found_arg = find_real_path(arg)
                new_args.append(found_arg if found_arg is not None else arg)
                continue

            if arg.endswith(('.c', '.cpp', '.h', '.S')) and not os.path.isabs(arg):
                 abs_path = os.path.join(original_directory, arg)
                 found_abs_path = find_real_path(abs_path)
                 
                 if found_abs_path is not None:
                     if new_directory and found_abs_path.startswith(new_directory + os.sep):
                         new_args.append(os.path.relpath(found_abs_path, new_directory))
                     else:
                         new_args.append(found_abs_path)
                 else:
                     new_args.append(arg)
                 continue
            
            new_args.append(arg)

        entry['arguments'] = new_args
        new_data.append(entry)

except KeyboardInterrupt:
    print("\nInterrupted by user. Saving partial results...")

# write new compile_commands.json
out_file = os.path.splitext(cdb_file)[0] + "_fixed.json"
with open(out_file, 'w') as f:
    json.dump(new_data, f, indent=2)

if len(new_data) < total_entries:
    print(f"Saved {len(new_data)} processed entries to {out_file}")
else:
    print(f"Fixed compile_commands.json saved to {out_file}")
