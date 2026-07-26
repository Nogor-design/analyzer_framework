from __future__ import annotations

import os
import sys
import glob
from pathlib import Path
from typing import Any

# Ensure template_naming path is added so we can import it
TEMPLATE_NAMING_PATH = "D:\\templateNaming"
if TEMPLATE_NAMING_PATH not in sys.path:
    sys.path.append(TEMPLATE_NAMING_PATH)

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif"}

KNOWN_MAS = [
    "Aphrodite", "Apollo", "Ares", "Artemis", "Athena",
    "Cerberus", "Chimera", "Demeter", "Dionysus", "Echidna",
    "Griffin", "Hades", "Harpy", "Hephaestus", "Hera",
    "Hermes", "Hydra", "Medusa", "Minotaur", "Phoenix",
    "Poseidon", "Siren", "Sphinx", "Typhon", "Zeus"
]

def list_god_images(directory_path: str) -> dict[str, Any]:
    """
    List all image files in the given directory recursively.
    Classifies files as named or positional (page-based from PDF extract).
    """
    p = Path(directory_path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        return {"error": f"Directory does not exist: {directory_path}", "files": []}

    files = []
    try:
        # Search recursively
        for entry in p.rglob("*"):
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext not in SUPPORTED_EXTS:
                continue

            # Calculate relative path for display if inside, otherwise full path
            try:
                rel_path = str(entry.relative_to(p)).replace("\\", "/")
            except ValueError:
                rel_path = str(entry).replace("\\", "/")

            name = entry.name
            stem = entry.stem
            size = entry.stat().st_size

            # Check if it has a positional PDF name (e.g. page_0001_img_01_xref_12)
            is_positional = ("page_" in stem.lower() and "xref" in stem.lower())
            is_named = not is_positional

            # Detect creature (MA) from filename if possible
            detected_ma = None
            for ma in KNOWN_MAS:
                if ma.lower() in stem.lower():
                    detected_ma = ma
                    break

            files.append({
                "name": name,
                "stem": stem,
                "relative_path": rel_path,
                "absolute_path": str(entry).replace("\\", "/"),
                "size_bytes": size,
                "is_named": is_named,
                "detected_ma": detected_ma
            })
    except Exception as exc:
        return {"error": str(exc), "files": []}

    # Sort files: positional first, then named, then alphabetically
    files.sort(key=lambda x: (not x["is_named"], x["name"].lower()))

    return {
        "directory": str(p).replace("\\", "/"),
        "files": files
    }

def rename_god_image(old_path: str, new_name: str) -> dict[str, Any]:
    """
    Rename an image file on disk. Keeps original extension if new_name lacks one.
    """
    old_p = Path(old_path).expanduser().resolve()
    if not old_p.exists() or not old_p.is_file():
        return {"success": False, "error": f"Source file does not exist: {old_path}"}

    # Clean the new name to prevent path traversal
    new_name_clean = Path(new_name).name
    if not new_name_clean:
        return {"success": False, "error": "Invalid new name"}

    # If new_name doesn't end with a supported image extension, preserve the original one
    new_ext = Path(new_name_clean).suffix.lower()
    if new_ext not in SUPPORTED_EXTS:
        new_name_clean = new_name_clean + old_p.suffix

    new_p = old_p.with_name(new_name_clean)

    if new_p == old_p:
        return {"success": True, "message": "Name is unchanged", "new_path": str(new_p).replace("\\", "/")}

    if new_p.exists():
        return {"success": False, "error": f"Destination file already exists: {new_name_clean}"}

    try:
        old_p.rename(new_p)
        return {
            "success": True,
            "message": f"Successfully renamed to {new_name_clean}",
            "old_path": str(old_p).replace("\\", "/"),
            "new_path": str(new_p).replace("\\", "/"),
            "new_name": new_name_clean
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}

def get_expected_image_names(workspace_dir: str, target_image_dir: str | None = None) -> dict[str, Any]:
    """
    Scan the workspace for XML templates and extract expected image names.
    Cross-references with target_image_dir to find which ones are missing.
    """
    w_path = Path(workspace_dir).expanduser().resolve()
    xml_files = []
    
    # Exclude directories
    exclude_dirs = {".venv", "node_modules", ".ta_artifacts", ".git", ".idea"}
    
    # Recursive search
    for root, dirs, files in os.walk(w_path):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            if f.lower().endswith(".xml"):
                xml_files.append(Path(root) / f)

    # Load templates and extract naming info
    expected_stems = set()
    templates_info = []
    
    # Cache known template naming package
    try:
        from template_naming import analyze_template
        template_naming_ok = True
    except ImportError:
        template_naming_ok = False

    for xml_p in xml_files:
        if not xml_p.exists():
            continue
        
        # We only care about templates that name gods/monsters
        # Run analyze_template
        if template_naming_ok:
            try:
                decision = analyze_template(xml_p)
                phase = getattr(decision, "phase", None)
                ma_name = getattr(decision, "ma_name", None)
                descriptor = getattr(decision, "descriptor", None)
                direction = getattr(decision, "direction", None)
                
                if ma_name and descriptor:
                    # Stems expected by the report lookup
                    # 1. Phase + MA + Descriptor (the standard variant)
                    if phase:
                        variant_stem = f"{phase}{ma_name}{descriptor}"
                        expected_stems.add((variant_stem, ma_name, "variant"))
                    
                    # 2. MA + Descriptor (the generic card)
                    generic_stem = f"{ma_name}{descriptor}"
                    expected_stems.add((generic_stem, ma_name, "generic"))
                    
                    # 3. MA (fallback)
                    expected_stems.add((ma_name, ma_name, "ma"))
                    
                    templates_info.append({
                        "file": xml_p.name,
                        "path": str(xml_p).replace("\\", "/"),
                        "ma_name": ma_name,
                        "phase": phase,
                        "descriptor": descriptor,
                        "direction": direction,
                        "stems": [
                            f"{phase}{ma_name}{descriptor}" if phase else None,
                            f"{ma_name}{descriptor}",
                            ma_name
                        ]
                    })
            except Exception:
                # Skip XMLs that aren't strategy templates or fail to parse
                continue

    # Group expected names by MA
    by_ma: dict[str, list[dict[str, Any]]] = {}
    for stem, ma, role in expected_stems:
        by_ma.setdefault(ma, []).append({
            "stem": stem,
            "role": role,
            "exists": False # will check below
        })

    # If target_image_dir is provided, check which expected images already exist
    existing_stems = set()
    if target_image_dir:
        img_p = Path(target_image_dir).expanduser().resolve()
        if img_p.exists() and img_p.is_dir():
            for entry in img_p.rglob("*"):
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTS:
                    existing_stems.add(entry.stem.lower())

    missing_by_ma: dict[str, list[dict[str, Any]]] = {}
    
    # Mark existing and compile missing lists
    for ma, items in by_ma.items():
        # Sort items: variant first, then generic
        items.sort(key=lambda x: (x["role"] != "variant", x["stem"].lower()))
        
        ma_missing = []
        for item in items:
            stem = item["stem"]
            if stem.lower() in existing_stems:
                item["exists"] = True
            else:
                ma_missing.append(item)
        if ma_missing:
            missing_by_ma[ma] = ma_missing

    # Convert sets to sorted lists for JSON
    all_expected = sorted(list({stem for stem, _, _ in expected_stems}))

    return {
        "all_expected": all_expected,
        "by_ma": {ma: items for ma, items in sorted(by_ma.items())},
        "missing_by_ma": {ma: items for ma, items in sorted(missing_by_ma.items())},
        "scanned_templates_count": len(templates_info)
    }
