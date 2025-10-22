#!/usr/bin/env python3
import os
import xml.etree.ElementTree as ET
import json
import argparse
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# List of ARMCC predefined macros for clangd
ARMCC_PREDEFINED_MACROS = [
    "__CC_ARM",
    "__arm__",
    "__ASM=",
    "__align(x)=",
    "__ALIGNOF__(x)=",
    "__alignof__(x)=",
    "__asm(x)=",
    "__forceinline=",
    "__restrict=",
    "__global_reg(n)=",
    "__inline=",
    # "__int64=long long",
    "__INTADDR__(expr)=0",
    "__irq=",
    "__packed=",
    "__pure=",
    "__smc(n)=",
    "__svc(n)=",
    "__svc_indirect(n)=",
    "__svc_indirect_r7(n)=",
    "__value_in_regs=",
    "__weak=",
    "__writeonly=",
    "__declspec(x)=",
    "__attribute__(x)=",
    "__nonnull__(x)=",
    "__register=",
    "__breakpoint(x)=",
    "__cdp(x,y,z)=",
    "__clrex()=",
    "__clz(x)=0U",
    "__current_pc()=0U",
    "__current_sp()=0U",
    "__disable_fiq()=",
    "__disable_irq()=",
    "__dmb(x)=",
    "__dsb(x)=",
    "__enable_fiq()=",
    "__enable_irq()=",
    "__fabs(x)=0.0",
    "__fabsf(x)=0.0f",
    "__force_loads()=",
    "__force_stores()=",
    "__isb(x)=",
    "__ldrex(x)=0U",
    "__ldrexd(x)=0U",
    "__ldrt(x)=0U",
    "__memory_changed()=",
    "__nop()=",
    "__pld(...)=",
    "__pli(...)=",
    "__qadd(x,y)=0",
    "__qdbl(x)=0",
    "__qsub(x,y)=0",
    "__rbit(x)=0U",
    "__rev(x)=0U",
    "__return_address()=0U",
    "__ror(x,y)=0U",
    "__schedule_barrier()=",
    "__semihost(x,y)=0",
    "__sev()=",
    "__sqrt(x)=0.0",
    "__sqrtf(x)=0.0f",
    "__ssat(x,y)=0",
    "__strex(x,y)=0U",
    "__strexd(x,y)=0",
    "__strt(x,y)=",
    "__swp(x,y)=0U",
    "__usat(x,y)=0U",
    "__wfe()=",
    "__wfi()=",
    "__yield()=",
    "__vfp_status(x,y)=0"
]

def get_element_text(element, path, default=''):
    """Safely get text from an XML element."""
    node = element.find(path)
    return node.text if node is not None and node.text is not None else default

def parse_options(options_element, no_c99=False):
    """Parse compiler options from a Cads or Aads element."""
    if options_element is None:
        return {}

    various_controls = options_element.find('VariousControls')
    if various_controls is None:
        return {}

    defines_text = get_element_text(various_controls, 'Define')
    includes_text = get_element_text(various_controls, 'IncludePath')
    misc_text = get_element_text(various_controls, 'MiscControls')

    flags = {
        'defines': [f'-D{d.strip()}' for d in defines_text.split(',') if d.strip()],
        'includes': [f'-I{p.strip()}' for p in includes_text.split(';') if p.strip()],
        'misc': misc_text.split()
    }

    # If --no-c99 is active, filter out C99 flags from misc, regardless of source
    if no_c99:
        flags['misc'] = [
            flag for flag in flags['misc']
            if flag.lower() not in ['--c99', '-sdt=c99']
        ]

    # Check for C99 mode from uC99 tag, but only add if --no-c99 is not set
    if not no_c99 and get_element_text(options_element, 'uC99') == '1':
        flags['misc'].append('--c99')

    return flags

def merge_flags(global_flags, local_flags):
    """Merge global and local flags, local flags take precedence for misc."""
    # For defines and includes, we just combine them.
    # For misc controls, Keil's file-specific options often override global ones.
    # A simple approach is to use local misc if present, otherwise global.
    if not local_flags:
        return global_flags

    merged = {
        'defines': global_flags.get('defines', []) + local_flags.get('defines', []),
        'includes': global_flags.get('includes', []) + local_flags.get('includes', []),
        'misc': local_flags.get('misc', []) if local_flags.get('misc') else global_flags.get('misc', [])
    }
    return merged

def parse_uvprojx(uvprojx_path, target_name=None, no_c99=False):
    """Parse a Keil uvprojx file to extract build information."""
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
            logging.warning(f"Target '{target_name}' not found. Falling back to the first target.")
    
    if target is None:
        target = root.find('.//Target')

    if target is None:
        raise ValueError("No targets found in the project file.")

    logging.info(f"Processing target: '{get_element_text(target, 'TargetName')}'")

    # 1. Get global compiler options
    target_arm_ads = target.find('TargetOption/TargetArmAds')
    if target_arm_ads is None:
        raise ValueError("TargetArmAds section not found in the target.")
        
    global_cads = parse_options(target_arm_ads.find('Cads'), no_c99)
    global_aads = parse_options(target_arm_ads.find('Aads'), no_c99) # For assembly files

    # 2. Extract files and their specific options
    files_data = []
    groups = target.findall('.//Group')
    if not groups:
        logging.warning("No file groups found in the target.")

    group_flags = {}

    for group in groups:
        group_name = get_element_text(group, 'GroupName', 'Unnamed Group')
        
        # Get group-level options (if any)
        group_options_node = group.find('GroupOption')
        group_cads = {}
        if group_options_node is not None:
            # Try to find Cads under GroupArmAds first
            cads_node = group_options_node.find('GroupArmAds/Cads')
            if cads_node is None:
                # If not found, try CommonProperty/Cads
                cads_node = group_options_node.find('CommonProperty/Cads')
            
            if cads_node is not None:
                group_cads = parse_options(cads_node, no_c99)
        
        group_flags = merge_flags(global_cads, group_cads)

        for file_node in group.findall('Files/File'):
            file_path_text = get_element_text(file_node, 'FilePath')
            if not file_path_text:
                continue

            file_path = Path(file_path_text)
            file_type = get_element_text(file_node, 'FileType')

            # We care about C/C++ (1), Assembly (2)
            if file_type not in ['1', '2', '5']: # 5 is for C++
                logging.debug(f"Skipping file '{file_path}' with type '{file_type}'")
                continue

            # Get file-specific options
            file_option_node = file_node.find('FileOption')
            file_cads = {}
            if file_option_node:
                 file_cads = parse_options(file_option_node.find('CommonProperty/Cads'), no_c99)
            
            final_flags = merge_flags(group_flags, file_cads)
            
            # Use assembly flags for assembly files
            if file_type == '2':
                final_flags = merge_flags(global_aads, final_flags)


            files_data.append({
                'path': file_path,
                'flags': final_flags
            })
            logging.debug(f"Found source file: {file_path}")

    return {
        'project_dir': uvprojx_path.parent,
        'files': files_data
    }

def find_compiler_from_log(project_dir, objects_dir_name):
    """Try to find the compiler path from the MDK build log."""
    objects_dir = project_dir / objects_dir_name
    if not objects_dir.is_dir():
        logging.debug(f"Objects directory '{objects_dir_name}' not found.")
        return None, None

    build_log_files = list(objects_dir.glob('*.build_log.htm'))
    if not build_log_files:
        logging.debug(f"No build log found in '{objects_dir}'.")
        return None, None

    # Find the most recent log file
    build_log_file = max(build_log_files, key=lambda p: p.stat().st_mtime)
    logging.info(f"Found build log: {build_log_file}")

    try:
        with open(build_log_file, 'r', errors='ignore') as f:
            for line in f:
                if "Toolchain Path:" in line:
                    toolchain_path_str = line.split("Toolchain Path:")[1].strip()
                    toolchain_path = Path(toolchain_path_str)
                    compiler_path = toolchain_path / "armcc.exe"
                    if compiler_path.exists():
                        logging.info(f"Found compiler from log: {compiler_path}")
                        return compiler_path, toolchain_path.parent / 'include'
                    else:
                        logging.warning(f"Found toolchain path '{toolchain_path}', but 'armcc.exe' not found.")
                        return None, None
    except Exception as e:
        logging.error(f"Error reading build log: {e}")
    
    return None, None


def generate_compile_commands(uvprojx_path, output_path, target_name, objects_dir_name, compiler_path, custom_macros=None, no_c99=False):
    """Generate compile_commands.json."""
    
    build_info = parse_uvprojx(uvprojx_path, target_name, no_c99)
    project_dir = build_info['project_dir']
    
    armcc_path = None
    arm_include_path = None

    # Determine compiler path
    if compiler_path:
        armcc_path = Path(compiler_path)
        # Assume include path is relative to compiler: ../include
        arm_include_path = armcc_path.parent.parent / 'include'
        logging.info(f"Using user-provided compiler: {armcc_path}")
    else:
        logging.info("Compiler path not provided, searching in build log...")
        armcc_path, arm_include_path = find_compiler_from_log(project_dir, objects_dir_name)

    if not armcc_path or not armcc_path.exists():
        logging.warning("ARMCC compiler not found. Using 'armcc' as a fallback. Please consider providing the path using --compiler.")
        armcc_path = Path("armcc") # Fallback
    
    compiler_posix_path = armcc_path.as_posix()

    entries = []
    for file_data in build_info['files']:
        file_path = file_data['path']
        flags = file_data['flags']
        
        # Make file path absolute and use forward slashes
        abs_file_path = (project_dir / file_path).resolve()
        
        # Skip if file does not exist
        if not abs_file_path.is_file():
            logging.warning(f"Source file not found, skipping: {abs_file_path}")
            continue

        # Base arguments
        args = [compiler_posix_path]

        # Add flags from project file
        args.extend(flags.get('defines', []))

        # Add ARMCC predefined macros if a real ARMCC compiler was found
        if armcc_path.exists() and armcc_path.name.lower() in ["armcc", "armcc.exe", "armclang", "armclang.exe"]:
            for macro in ARMCC_PREDEFINED_MACROS:
                args.append(f'-D{macro}')

        # Add custom macros from command line
        if custom_macros:
            for macro in custom_macros:
                args.append(f'-D{macro}')
        
        # Add includes, making them absolute
        includes = flags.get('includes', [])
        for inc in includes:
            # -I"path"
            inc_path_str = inc[2:]
            # Resolve relative paths against project dir
            abs_inc_path = (project_dir / inc_path_str).resolve()
            args.append(f"-I{abs_inc_path.as_posix()}")

        # Add ARM's own include path if found
        if arm_include_path and arm_include_path.is_dir():
            args.append(f"-I{arm_include_path.as_posix()}")

        args.extend(flags.get('misc', []))
        
        # Add source file itself
        args.append(abs_file_path.as_posix())

        entries.append({
            "directory": project_dir.as_posix(),
            "file": abs_file_path.as_posix(),
            "arguments": args
        })

    with open(output_path, 'w') as f:
        json.dump(entries, f, indent=2)
    
    logging.info(f"Successfully generated {output_path} with {len(entries)} file entries.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Convert Keil .uvprojx to compile_commands.json for clangd.',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('uvprojx', help='Path to the .uvprojx file.')
    parser.add_argument('-t', '--target', help='Specify the target name to build. Defaults to the first target found.')
    parser.add_argument('-o', '--output', default='compile_commands.json', help='Output path for compile_commands.json. (default: %(default)s)')
    parser.add_argument('-d', '--objects', default='Objects', help='Objects directory name to search for build logs. (default: %(default)s)')
    parser.add_argument('--compiler', help='Path to the armcc.exe compiler. Overrides build log discovery.')
    parser.add_argument('--macro', action='append', help='Add custom preprocessor macros (e.g., --macro MY_DEFINE=1). Can be used multiple times.')
    parser.add_argument('--no-c99', action='store_true', help='Do not add --c99 flag even if uC99 is enabled in project.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose debug output.')
    
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        generate_compile_commands(args.uvprojx, args.output, args.target, args.objects, args.compiler, args.macro, no_c99=args.no_c99)
    except (FileNotFoundError, ValueError) as e:
        logging.error(e)
        exit(1)