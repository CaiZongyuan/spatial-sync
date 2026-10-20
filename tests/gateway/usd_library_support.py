"""Import real authored USD/PBR packages through the public upload endpoint."""

import io
import tarfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "examples/usd-library"


def upload_directory(client, path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for file in sorted(path.rglob("*")):
            if file.is_file():
                value = file.read_bytes()
                member = tarfile.TarInfo(file.relative_to(path).as_posix())
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
    response = client.post("/api/v1/library/imports", content=stream.getvalue(),
                           headers={"Content-Type": "application/x-tar"})
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["launchable"], response.json()
    return response.json()


def import_fixture(client):
    for asset in sorted((FIXTURE / "assets").iterdir()):
        upload_directory(client, asset)
    return upload_directory(client, FIXTURE / "scene")
