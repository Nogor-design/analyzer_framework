# scripts/verify_split_yamls.py
# Programmatic validation of split YAML configurations against the framework's config parser.

import sys
from pathlib import Path

# Add src/ to Python path to ensure clean import of ta_foundation
src_dir = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_dir))

try:
    import yaml
    from ta_foundation.reports.html.config import load_report_config
except ImportError as e:
    print(f"ERROR: Failed to import ta_foundation modules. Is your Python environment correct? {e}")
    sys.exit(1)

configs_dir = Path(__file__).resolve().parent.parent / "docs" / "reports_documentation" / "configs"
yaml_files = sorted(configs_dir.rglob("*.yaml"))

if not yaml_files:
    print("ERROR: No YAML files found in", configs_dir)
    sys.exit(1)

print(f"Found {len(yaml_files)} YAML configurations to verify.")
print("=" * 60)

all_ok = True

for f in yaml_files:
    print(f"Verifying {f.relative_to(configs_dir)}...")
    
    # 1. Test basic YAML syntax
    try:
        raw_data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            print(f"  [FAIL] YAML loaded but root is not a dictionary (type: {type(raw_data)})")
            all_ok = False
            continue
    except Exception as e:
        print(f"  [FAIL] YAML syntax error: {e}")
        all_ok = False
        continue

    # 2. Test framework loader deep-merge and section validation
    try:
        report_cfg = load_report_config(f)
        
        # Verify sections are resolved correctly
        sections_list = report_cfg.sections
        sec_ids = [s.get("id") if isinstance(s, dict) else s for s in sections_list]
        
        print(f"  [OK] Title: '{report_cfg.title}'")
        print(f"  [OK] Output: '{report_cfg.output_filename}'")
        print(f"  [OK] Sections ({len(sec_ids)}): {sec_ids}")
        
    except Exception as e:
        print(f"  [FAIL] Framework loader error: {e}")
        all_ok = False

    print("-" * 60)

if all_ok:
    print("SUCCESS: All configurations are 100% syntactically valid and compatible with the report loader!")
    sys.exit(0)
else:
    print("FAILURE: Some configurations failed validation. See errors above.")
    sys.exit(1)
