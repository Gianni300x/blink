"""Verifica metadatos y que los paquetes no incluyan documentación privada."""

import configparser
from email.parser import BytesParser
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import zipfile


def main() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text())
    project = config["project"]
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise SystemExit("Se espera exactamente un wheel y un sdist de esta versión.")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
        assert normalize(metadata["Name"]) == normalize(project["name"])
        assert metadata["Version"] == project["version"]
        assert metadata["Requires-Python"] == project["requires-python"]
        entries = configparser.ConfigParser()
        entries.read_string(archive.read(metadata_path.replace("METADATA", "entry_points.txt")).decode())
        assert dict(entries["console_scripts"]) == project["scripts"]
        assert any("LICENSE" in name for name in names)
        assert not any("docs" in Path(name).parts or Path(name).name.startswith(".env") for name in names)
        for package in config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
            for source in Path(package).rglob("*.py"):
                assert source.relative_to("src").as_posix() in names, source
    with tarfile.open(sources[0]) as archive:
        names = archive.getnames()
        assert not any("docs" in Path(name).parts or Path(name).name.startswith(".env") for name in names)
        assert any(name.endswith("/LICENSE") for name in names)
        assert any(name.endswith("/tests/data/sample_dirty.csv") for name in names)
    print(f"Wheel y sdist verificados: {project['name']} {project['version']}.")


if __name__ == "__main__":
    main()
