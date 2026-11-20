"""Small URDF archive shared by the browser and native settling witnesses."""

import io
import json
import sys
import tarfile


def robot_archive():
    files = {
        "robot.json": json.dumps({
            "schema": "lw.robot-import/1", "id": "robot/browser-archive-probe",
            "title": "Browser Archive Probe", "entry": "probe.urdf",
            "license": "LICENSE", "licenseId": "CC0-1.0", "calibration": {"density": 250},
        }).encode(),
        "probe.urdf": b"""<robot name="probe">
  <link name="base">
    <collision><geometry><box size=".2 .4 .6"/></geometry></collision>
    <visual><geometry><box size=".2 .4 .6"/></geometry></visual>
  </link>
  <link name="tip"><collision><geometry><box size=".1 .1 .1"/></geometry></collision></link>
  <joint name="motor-a" type="revolute">
    <parent link="base"/><child link="tip"/><origin xyz=".2 0 0"/>
    <axis xyz="-1 0 0"/><limit lower="-.4" upper=".7" effort="20" velocity="1"/>
  </joint>
</robot>""",
        "LICENSE": b"Fixture created for testing; CC0-1.0\n",
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


if __name__ == "__main__":
    sys.stdout.buffer.write(robot_archive())
