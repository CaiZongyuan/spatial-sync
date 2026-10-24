"""One feed-forward float32 ONNX model, explicitly restricted to CPU execution."""

import numpy as np

from packages.library.archive import package_files, relative_path

from .observations import vector


class CpuPolicy:
    def __init__(self, path, spec, *, root=None, external=()):
        import onnx
        import onnxruntime as ort
        from google.protobuf.message import DecodeError

        root = root or path.parent
        files = set(package_files(root))
        try:
            model = onnx.load_model(str(path), load_external_data=False)
        except DecodeError as exc:
            raise ValueError("ONNX file is not a valid model") from exc
        pending, actual = [model], set()
        while pending:
            message = pending.pop()
            if (
                message.DESCRIPTOR.full_name == "onnx.TensorProto"
                and message.data_location == onnx.TensorProto.EXTERNAL
            ):
                props = {item.key: item.value for item in message.external_data}
                if set(props) - {"location", "offset", "length", "checksum"}:
                    raise ValueError("Unsupported external ONNX data properties")
                relative = relative_path(props["location"])
                name = (path.parent / relative).relative_to(root).as_posix()
                if name not in files:
                    raise ValueError("ONNX external data is outside its frozen package")
                actual.add(name)
            for field, value in message.ListFields():
                if field.cpp_type == field.CPPTYPE_MESSAGE:
                    pending.extend(value if field.is_repeated else [value])
        if actual != {entry["path"] for entry in external}:
            raise ValueError("ONNX external references differ from the export manifest")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        try:
            self.session = ort.InferenceSession(
                str(path), sess_options=options, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:
            # Native model validation has several provider-specific exception classes.
            raise ValueError("ONNX CPU model could not be loaded") from exc
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (
            len(inputs) != 1
            or len(outputs) != 1
            or inputs[0].type != "tensor(float)"
            or outputs[0].type != "tensor(float)"
            or inputs[0].shape != [1, spec.size]
            or outputs[0].shape != [1, len(spec.action.jointKeys)]
            or self.session.get_providers() != ["CPUExecutionProvider"]
        ):
            raise ValueError("Policy must have one fixed float32 [1,observation] input and [1,action] output")
        self.input, self.output = inputs[0].name, outputs[0].name
        self.observations, self.actions = spec.size, len(spec.action.jointKeys)
        self.runtime_version = ort.__version__
        self.inference_calls = self.valid_outputs = 0

    def infer(self, observations):
        inputs = vector(observations, self.observations).astype(np.float32)[None, :]
        if not np.isfinite(inputs).all():
            raise ValueError("Observation exceeds finite float32 range")
        try:
            self.inference_calls += 1
            output = self.session.run([self.output], {self.input: inputs})[0]
        except Exception as exc:
            raise ValueError("ONNX CPU inference failed") from exc
        if output.shape != (1, self.actions):
            raise ValueError("ONNX output shape changed")
        result = vector(output[0], self.actions)
        self.valid_outputs += 1
        return result
