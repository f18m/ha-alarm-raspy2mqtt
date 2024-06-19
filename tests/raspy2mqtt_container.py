import os
from testcontainers.core.container import DockerContainer
from testcontainers.mqtt import MosquittoContainer

# from testcontainers.core.utils import raise_for_deprecated_parameter

from tests.mosquitto_container import MosquittoContainerEnhanced


class Raspy2MQTTContainer(DockerContainer):
    """
    Specialization of DockerContainer to test this same repository artifact.
    """

    CONFIG_FILE = "integration-test-config.yaml"

    def __init__(self, broker: MosquittoContainerEnhanced) -> None:
        super().__init__(image="rpi2home-assistant")

        TEST_DIR = os.path.dirname(os.path.abspath(__file__))
        cfgfile = os.path.join(TEST_DIR, Raspy2MQTTContainer.CONFIG_FILE)
        self.with_volume_mapping(cfgfile, "/etc/rpi2home-assistant.yaml", mode="ro")
        self.with_volume_mapping("/tmp", "/tmp", mode="rw")
        self.with_env("DISABLE_HW", "true")

        # IMPORTANT: to link with the MQTT broker we want to use the IP address internal to the docker network,
        #            and the standard MQTT port. The localhost:exposed_port address is not reachable from a
        #            docker container that has been started inside a docker network!
        broker_container = broker.get_wrapped_container()
        broker_ip = broker.get_docker_client().bridge_ip(broker_container.id)
        print(
            f"Linking the {self.image} container with the MQTT broker at host:ip {broker_ip}:{MosquittoContainer.MQTT_PORT}"
        )

        self.with_env("MQTT_BROKER_HOST", broker_ip)
        self.with_env("MQTT_BROKER_PORT", MosquittoContainer.MQTT_PORT)

    def is_running(self):
        self.get_wrapped_container().reload()  # force refresh of container status
        # status = self.get_wrapped_container().attrs["State"]['Status']
        status = self.get_wrapped_container().status  # same as above
        return status == "running"

    def print_logs(self) -> str:
        print("** Raspy2MQTTContainer LOGS [STDOUT]:")
        print(self.get_logs()[0].decode())
        print("** Raspy2MQTTContainer LOGS [STDERR]:")
        print(self.get_logs()[1].decode())
