"""Build a downloadable, versioned agent package from its canonical sources."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root=Path(__file__).resolve().parents[1]
destination=root/"frontend/dashboard/public/releases"
destination.mkdir(parents=True,exist_ok=True)
with ZipFile(destination/"logiq-agent.zip","w",ZIP_DEFLATED) as archive:
    for name in ("collector.py","Dockerfile","README.md"):
        archive.write(root/"collector"/name,name)
print("Independent agent package generated")
