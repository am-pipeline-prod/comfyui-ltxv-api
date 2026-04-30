"""One-shot check: load via ComfyUI loader sim, print DESCRIPTIONs + schemas."""
import importlib.util
import sys
from pathlib import Path

# Pre-load ComfyUI's nodes module so the LTX nodes' lazy `comfy.utils` imports
# don't fail during introspection.
sys.path.insert(0, "/opt/comfyui/app")
try:
    import nodes as _comfy_nodes  # noqa: F401
except Exception:
    # Running from a non-comfy environment is fine for this smoke check.
    pass

PKG = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "comfyui-ltxv-api", PKG / "__init__.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules["comfyui-ltxv-api"] = mod
spec.loader.exec_module(mod)

for cid, cls in mod.NODE_CLASS_MAPPINGS.items():
    desc = (getattr(cls, "DESCRIPTION", "") or "")
    short = desc[:90].replace("\n", " ")
    print(f"  {cid}:")
    print(f"    DESCRIPTION: {short!r}{'...' if len(desc) > 90 else ''}")
    schema = cls.INPUT_TYPES()
    print(f"    required:   {list(schema.get('required', {}))}")
    print(f"    optional:   {list(schema.get('optional', {}))}")
    print(f"    returns:    {getattr(cls, 'RETURN_NAMES', None)}")
print("OK")
