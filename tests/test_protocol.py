"""Protocol helpers that need no hub: protobuf framing, device keys, schedules, encryption round trip.

Run: pip install cryptography pytest && pytest
"""
import importlib.util
import os
import pathlib

import pytest

# load bewave_client.py on its own (the package __init__ needs Home Assistant)
_P = pathlib.Path(__file__).parents[1] / "custom_components" / "bewave" / "bewave_client.py"
_spec = importlib.util.spec_from_file_location("bewave_client", _P)
proto = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proto)


def test_varint_roundtrip():
    for v in (0, 1, 127, 128, 300, 2**32 - 1, 2000000000123):
        assert proto.pb_read(proto.pb_varint(3, v)) == {3: [v]}


def test_nested_bytes():
    inner = proto.pb_varint(1, 5) + proto.pb_bytes(2, b"abc")
    d = proto.pb_read(proto.pb_bytes(7, inner))
    assert proto.pb_read(d[7][0]) == {1: [5], 2: [b"abc"]}


def test_device_key_plain_uuid():
    key = proto.pb_varint(1, 123456)
    assert proto.key_to_id(key) == 123456
    assert proto.key_bytes(123456) == key


def test_device_key_composite_hub_output():
    # hub main-board outputs / zones share uuid 1 and differ by index (O1 = 5, Z2 = 2, ...)
    key = proto.pb_varint(1, 1) + proto.pb_varint(2, 6)
    cid = proto.key_to_id(key)
    assert cid == 1006
    assert proto.key_bytes(cid) == key


def test_schedule_roundtrip():
    sch = [{"id": "ab12c", "name": "Weekdays", "days": {"0": [["06:00", "08:00"], ["16:00", "22:30"]], "6": [["08:00", "22:00"]]}}]
    pt = proto.schedules_plaintext(sch)
    top = proto.pb_read(pt)
    assert proto.SCHEDULE_UPDATE in top
    # the hub answers a schedule update / get with the same structure under field 47/51
    reply = proto.pb_bytes(47, proto.pb_read(pt)[proto.SCHEDULE_UPDATE][0])
    parsed = proto.parse_schedules(reply)
    assert parsed is not None and parsed[0]["id"] == "ab12c" and parsed[0]["name"] == "Weekdays"
    assert parsed[0]["days"]["0"] == [["06:00", "08:00"], ["16:00", "22:30"]]
    assert parsed[0]["days"]["6"] == [["08:00", "22:00"]]


def test_message_encrypt_and_header():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, aad, pt = os.urandom(32), b"aad", b"\x08\x01hello"
    msg = proto.build_message(7, pt, key, aad)
    fd, _hlen, body_at = proto.parse_header(msg, 0)
    assert fd[5] == 7 and fd[2] == len(pt)
    import struct
    iv = struct.pack(">III", fd[7], fd[6], fd[5])
    assert AESGCM(key).decrypt(iv, msg[body_at:], aad) == pt
