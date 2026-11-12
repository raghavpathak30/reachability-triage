import socket

import pytest

ORIGINAL_CREATE_CONNECTION = socket.create_connection


def test_socket_connect_is_blocked():
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="network blocked"):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()


def test_connect_ex_is_blocked():
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="network blocked"):
            s.connect_ex(("127.0.0.1", 9))
    finally:
        s.close()


def test_create_connection_is_blocked():
    with pytest.raises(RuntimeError, match="network blocked"):
        socket.create_connection(("127.0.0.1", 9))


def test_getaddrinfo_is_blocked():
    with pytest.raises(RuntimeError, match="network blocked"):
        socket.getaddrinfo("example.invalid", 80)


def test_block_is_autouse_without_requesting_fixture():
    # This test lists no fixtures; the patch must still be active.
    assert socket.create_connection is not ORIGINAL_CREATE_CONNECTION
