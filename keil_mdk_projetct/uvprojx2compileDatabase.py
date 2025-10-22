#!/usr/bin/env python3
import os
import xml.etree.ElementTree as ET
import json
import argparse
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def get_element_text(element, path, default=""):
    """Safely get text from an XML element."""
    node = element.find(path)
    return node.text if node is not None and node.text is not None else default


def parse_options(options_element):
    """Parse compiler options from a Cads or Aads element."""
    if options_element is None:
        return {}

    various_controls = options_element.find("VariousControls")
    if various_controls is None:
        return {}

    defines_text = get_element_text(various_controls, "Define")
    includes_text = get_element_text(various_controls, "IncludePath")
    misc_text = get_element_text(various_controls, "MiscControls")

    flags = {
        "defines": [f"-D{d.strip()}" for d in defines_text.split(",") if d.strip()],
        "includes": [f"-I{p.strip()}" for p in includes_text.split(";") if p.strip()],
        "misc": misc_text.split(),
    }

    # Check for C99 mode
    if get_element_text(options_element, "uC99") == "1":
        flags["misc"].append("--c99")

    return flags


def merge_flags(global_flags, local_flags):
    """Merge global and local flags, local flags take precedence for misc."""
    # For defines and includes, we just combine them.
    # For misc controls, Keil's file-specific options often override global ones.
    # A simple approach is to use local misc if present, otherwise global.
    if not local_flags:
        return global_flags

    merged = {
        "defines": global_flags.get("defines", []) + local_flags.get("defines", []),
        "includes": global_flags.get("includes", []) + local_flags.get("includes", []),
        "misc": local_flags.get("misc", [])
        if local_flags.get("misc")
        else global_flags.get("misc", []),
    }
    return merged


def parse_uvprojx(uvprojx_path, target_name=None):
    """
    Parse a Keil uvprojx file to extract build information.
    Returns a dictionary of files with their specific compile commands.
    """
    uvprojx_path = Path(uvprojx_path).resolve()
    if not uvprojx_path.is_file():
        raise FileNotFoundError(f"uvprojx file not found: {uvprojx_path}")

    try:
        tree = ET.parse(uvprojx_path)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML in uvprojx file: {uvprojx_path}") from e

    # Find the specified target, or the first one if not specified
    target = None
    if target_name:
        target = root.find(f".//Target[Name='{target_name}']")
        if target is None:
            logging.warning(
                f"Target '{target_name}' not found. Falling back to the first target."
            )

    if target is None:
        target = root.find(".//Target")

    if target is None:
        raise ValueError("No targets found in the project file.")

    logging.info(f"Processing target: '{get_element_text(target, 'TargetName')}'")

    # 1. Get global compiler options
    target_arm_ads = target.find("TargetOption/TargetArmAds")
    if target_arm_ads is None:
        raise ValueError("TargetArmAds section not found in the target.")

    global_cads = parse_options(target_arm_ads.find("Cads"))
    global_aads = parse_options(target_arm_ads.find("Aads"))  # For assembly files

    # 2. Extract files and their specific options
    files_data = []
    groups = target.findall(".//Group")
    if not groups:
        logging.warning("No file groups found in the target.")

    group_flags = {}

    for group in groups:
        group_name = get_element_text(group, "GroupName", "Unnamed Group")

        # Get group-level options (if any)
        group_options_node = group.find("GroupOption")
        group_cads = {}
        if group_options_node is not None:
            # Try to find Cads under GroupArmAds first
            cads_node = group_options_node.find("GroupArmAds/Cads")
            if cads_node is None:
                # If not found, try CommonProperty/Cads
                cads_node = group_options_node.find("CommonProperty/Cads")

            if cads_node is not None:
                group_cads = parse_options(cads_node)
                group_flags = merge_flags(global_cads, group_cads)

        for file_node in group.findall("Files/File"):
            file_path_text = get_element_text(file_node, "FilePath")
            if not file_path_text:
                continue

            file_path = Path(file_path_text)
            file_type = get_element_text(file_node, "FileType")

            # We care about C/C++ (1), Assembly (2)
            if file_type not in ["1", "2", "5"]:  # 5 is for C++
                logging.debug(f"Skipping file '{file_path}' with type '{file_type}'")
                continue

            # Get file-specific options
            file_option_node = file_node.find("FileOption")
            file_cads = {}
            if file_option_node:
                file_cads = parse_options(file_option_node.find("CommonProperty/Cads"))

            final_flags = merge_flags(group_flags, file_cads)

            # Use assembly flags for assembly files
            if file_type == "2":
                final_flags = merge_flags(global_aads, final_flags)

            files_data.append({"path": file_path, "flags": final_flags})
            logging.debug(f"Found source file: {file_path}")

    return {"project_dir": uvprojx_path.parent, "files": files_data}


def find_compiler_from_log(project_dir, objects_dir_name):
    """Try to find the compiler path from the MDK build log."""
    objects_dir = project_dir / objects_dir_name
    if not objects_dir.is_dir():
        logging.debug(f"Objects directory '{objects_dir_name}' not found.")
        return None, None

    build_log_files = list(objects_dir.glob("*.build_log.htm"))
    if not build_log_files:
        logging.debug(f"No build log found in '{objects_dir}'.")
        return None, None

    # Find the most recent log file
    build_log_file = max(build_log_files, key=lambda p: p.stat().st_mtime)
    logging.info(f"Found build log: {build_log_file}")

    try:
        with open(build_log_file, "r", errors="ignore") as f:
            for line in f:
                if "Toolchain Path:" in line:
                    toolchain_path_str = line.split("Toolchain Path:")[1].strip()
                    toolchain_path = Path(toolchain_path_str)
                    compiler_path = toolchain_path / "armcc.exe"
                    if compiler_path.exists():
                        logging.info(f"Found compiler from log: {compiler_path}")
                        return compiler_path, toolchain_path.parent / "include"
                    else:
                        logging.warning(
                            f"Found toolchain path '{toolchain_path}', but 'armcc.exe' not found."
                        )
                        return None, None
    except Exception as e:
        logging.error(f"Error reading build log: {e}")

    return None, None


def generate_compile_commands(
    uvprojx_path, output_path, target_name, objects_dir_name, compiler_path
):
    """Generate compile_commands.json."""

    build_info = parse_uvprojx(uvprojx_path, target_name)
    project_dir = build_info["project_dir"]

    armcc_path = None
    arm_include_path = None

    # Determine compiler path
    if compiler_path:
        armcc_path = Path(compiler_path)
        # Assume include path is relative to compiler: ../include
        arm_include_path = armcc_path.parent.parent / "include"
        logging.info(f"Using user-provided compiler: {armcc_path}")
    else:
        logging.info("Compiler path not provided, searching in build log...")
        armcc_path, arm_include_path = find_compiler_from_log(
            project_dir, objects_dir_name
        )

    if not armcc_path or not armcc_path.exists():
        logging.warning(
            "ARMCC compiler not found. Using 'armcc' as a fallback. Please consider providing the path using --compiler."
        )
        armcc_path = Path("armcc")  # Fallback

    compiler_posix_path = armcc_path.as_posix()

    entries = []
    for file_data in build_info["files"]:
        file_path = file_data["path"]
        flags = file_data["flags"]

        # Make file path absolute and use forward slashes
        abs_file_path = (project_dir / file_path).resolve()

        # Skip if file does not exist
        if not abs_file_path.is_file():
            logging.warning(f"Source file not found, skipping: {abs_file_path}")
            continue

        # Base arguments
        args = [compiler_posix_path]

        # Add flags
        args.extend(flags.get("defines", []))

        # Add includes, making them absolute
        includes = flags.get("includes", [])
        for inc in includes:
            # -I"path"
            inc_path_str = inc[2:]
            # Resolve relative paths against project dir
            abs_inc_path = (project_dir / inc_path_str).resolve()
            args.append(f"-I{abs_inc_path.as_posix()}")

        # Add ARM's own include path if found
        if arm_include_path and arm_include_path.is_dir():
            args.append(f"-I{arm_include_path.as_posix()}")

        args.extend(flags.get("misc", []))

        # Add source file itself
        args.append(abs_file_path.as_posix())

        # For clangd, we don't need to specify a real output file
        # args.extend(['-o', f'Objects/{file_path.stem}.o'])

        entries.append(
            {
                "directory": project_dir.as_posix(),
                "file": abs_file_path.as_posix(),
                "arguments": args,
            }
        )

    with open(output_path, "w") as f:
        json.dump(entries, f, indent=2)

    logging.info(
        f"Successfully generated {output_path} with {len(entries)} file entries."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert Keil .uvprojx to compile_commands.json for clangd.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("uvprojx", help="Path to the .uvprojx file.")
    parser.add_argument(
        "-t",
        "--target",
        help="Specify the target name to build. Defaults to the first target found.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="compile_commands.json",
        help="Output path for compile_commands.json. (default: %(default)s)",
    )
    parser.add_argument(
        "-d",
        "--objects",
        default="Objects",
        help="Objects directory name to search for build logs. (default: %(default)s)",
    )
    parser.add_argument(
        "--compiler",
        help="Path to the armcc.exe compiler. Overrides build log discovery.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose debug output."
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        generate_compile_commands(
            args.uvprojx, args.output, args.target, args.objects, args.compiler
        )
    except (FileNotFoundError, ValueError) as e:
        logging.error(e)
        exit(1)