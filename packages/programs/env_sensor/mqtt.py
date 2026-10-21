from packages.programs.mqtt import MqttProgram


class EnvSensorProgram(MqttProgram):
    id, type = "env_sensor.mqtt", "env-sensor/v1"
