"""Standalone import smoke check: load __init__.py, verify mappings + schemas."""
import importlib.util
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

spec = importlib.util.spec_from_file_location(
    "comfyui_ltxv_api_pkg", PKG / "__init__.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("NODE_CLASS_MAPPINGS:", list(mod.NODE_CLASS_MAPPINGS))
print("DISPLAY_NAMES:", mod.NODE_DISPLAY_NAME_MAPPINGS)
for cid, cls in mod.NODE_CLASS_MAPPINGS.items():
    schema = cls.INPUT_TYPES()
    req = list(schema.get("required", {}))
    opt = list(schema.get("optional", {}))
    print(f"  {cid}: required={req} optional={opt}")
    print(f"    RETURN_TYPES={getattr(cls, 'RETURN_TYPES', None)}")
    print(f"    RETURN_NAMES={getattr(cls, 'RETURN_NAMES', None)}")
    print(f"    CATEGORY={getattr(cls, 'CATEGORY', None)}")
print("OK")
