from packages.programs.mqtt import MqttProgram


class IncubatorProgram(MqttProgram):
    id, type = "incubator.mqtt", "incubator/v1"
