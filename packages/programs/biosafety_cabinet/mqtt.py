from packages.programs.mqtt import MqttProgram


class BiosafetyCabinetProgram(MqttProgram):
    id, type = "biosafety_cabinet.mqtt", "biosafety-cabinet/v1"
