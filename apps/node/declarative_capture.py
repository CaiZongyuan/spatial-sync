"""Version two native capture binds the managed compiler and exact dispatch."""

import json

if __package__:
    from apps.node.native_observation_capture import NativeObservationCapture
    from apps.node.observation_capture import file_hash
else:
    from lw_native_observation_capture import NativeObservationCapture
    from lw_observation_capture import file_hash


class DeclarativeCapture(NativeObservationCapture):
    def publish(self, output, export, versions):
        receipt = super().publish(output, export, versions)
        if receipt is None:
            return None
        filename = output / receipt["samplesArtifact"]
        envelope = json.loads(filename.read_bytes())
        envelope["schema"] = "lw.policy-native-observation-samples/2"
        envelope["capture"]["compilerHash"] = self.profile["compilerHash"]
        filename.write_text(json.dumps(envelope, sort_keys=True, allow_nan=False), encoding="utf-8")
        receipt["schema"] = "lw.policy-native-observation-capture/2"
        receipt["capture"] = envelope["capture"]
        receipt["samplesSha256"] = file_hash(filename)
        (output / "observations/native-capture.json").write_text(
            json.dumps(receipt, sort_keys=True, allow_nan=False), encoding="utf-8"
        )
        return receipt
