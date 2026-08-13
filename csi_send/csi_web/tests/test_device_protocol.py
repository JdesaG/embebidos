import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class FakeLogs:
    def info(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class FakeState:
    def __init__(self):
        self.logs = FakeLogs()
        self.gateway = None

    def set_gateway(self, gateway):
        self.gateway = gateway


def device_message(message_type, sequence, values):
    return server.WIRE_DEVICE_BODY.pack(
        server.DEVICE_MESSAGE_MAGIC,
        server.DEVICE_MESSAGE_VERSION,
        message_type,
        server.WIRE_DEVICE_BODY.size,
        sequence,
        *values,
        1234,
    )


class DeviceProtocolTests(unittest.TestCase):
    def test_decodes_actuator_confirmation(self):
        message = device_message(
            server.DEVICE_MSG_ACTUATOR_STATE,
            7,
            [1, 50, 10, 20, 30, 0, 1, server.ENERGY_MODES["eco"]],
        )
        frame_len = server.WIRE_FRAME_HEADER.size + len(message)
        datagram = (
            server.WIRE_BATCH_HEADER.pack(server.WIRE_BATCH_MAGIC, 1, 1, 0)
            + server.WIRE_FRAME_HEADER.pack(server.WIRE_FRAME_ACTUATOR, 0, frame_len, 7)
            + message
        )

        decoded = server.decode_udp_batch(datagram)

        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0][0], "actuator")
        self.assertTrue(decoded[0][1]["light_on"])
        self.assertEqual(decoded[0][1]["brightness"], 50)
        self.assertEqual(decoded[0][1]["energy_mode"], "eco")

    def test_discovers_gateway_and_sends_command(self):
        state = FakeState()
        discovery = server.DiscoveryCommandServer("127.0.0.1", 0, 5000, state)
        discovery.start()
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        client.settimeout(2)
        command_port = client.getsockname()[1]
        try:
            values = [command_port & 0xFF, (command_port >> 8) & 0xFF] + [0] * 6
            client.sendto(
                device_message(server.DEVICE_MSG_SERVER_DISCOVERY, 9, values),
                ("127.0.0.1", discovery.sock.getsockname()[1]),
            )
            reply, _ = client.recvfrom(256)
            reply_values = server.WIRE_DEVICE_BODY.unpack(reply)
            self.assertEqual(reply_values[2], server.DEVICE_MSG_SERVER_REPLY)
            self.assertEqual(state.gateway, ("127.0.0.1", command_port))

            result = discovery.send_command(
                {
                    "light_on": True,
                    "brightness": 60,
                    "red": 1,
                    "green": 2,
                    "blue": 3,
                    "energy_mode": "standby",
                }
            )
            command, _ = client.recvfrom(256)
            command_values = server.WIRE_DEVICE_BODY.unpack(command)
            self.assertEqual(command_values[2], server.DEVICE_MSG_ACTUATOR_COMMAND)
            self.assertEqual(command_values[5:10], (1, 60, 1, 2, 3))
            self.assertEqual(command_values[12], server.ENERGY_MODES["standby"])
            self.assertEqual(result["energy_mode"], "standby")
        finally:
            client.close()
            discovery.stop()


if __name__ == "__main__":
    unittest.main()
