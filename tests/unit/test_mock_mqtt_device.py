"""Mock wire behavior and fault injection without starting an MQTT connection."""

from tools.mock_mqtt_device import Devices


def test_mock_devices_apply_only_addressed_valid_commands_and_return_correlated_ack():
    devices = Devices()
    request = {"requestId": "one", "action": "setSetpoint", "params": {"temperatureC": 38}}
    devices.command("lab/incubator2/cmd", request)
    assert devices.incubator["setpoint"] == 38
    [(topic, ack)] = devices.acknowledgements()
    assert topic == "lab/incubator2/ack" and ack == {"requestId": "one", "status": "succeeded"}
    devices.command("lab/biosafety1/cmd", request | {"requestId": "wrong-device"})
    assert devices.acknowledgements()[0][1]["status"] == "failed"
    devices.command("lab/incubator2/cmd", request | {"params": {"temperatureC": 45}})
    assert devices.incubator["setpoint"] == 38  # duplicate identity does not apply a second side effect


def test_mock_faults_keep_other_devices_running_and_can_drop_confirmations():
    devices = Devices(drop_acks=True)
    assert devices.samples("heat")["incubator2"]["temp"] == 45
    assert devices.samples("low-airflow")["biosafety1"]["inflow"] == .2
    samples = devices.samples("disconnect")
    assert devices.disconnected and samples["environment1"]["temp"] == 22
    devices.command("lab/incubator2/cmd", {
        "requestId": "offline", "action": "setSetpoint", "params": {"temperatureC": 40},
    })
    assert devices.incubator["setpoint"] == 37 and not devices.acknowledgements()
