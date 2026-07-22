from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Final


_MAGIC: Final[bytes] = b"SCSEAL2\x00"
_CHUNK_MAGIC: Final[bytes] = b"SCCHNK2\x00"
_NONCE_BYTES: Final[int] = 24
_TAG_BYTES: Final[int] = 16
_KEY_BYTES: Final[int] = 32
_HEADER = struct.Struct(">8sBHQ")
_CHUNK_HEADER = struct.Struct(">8sBIQQ")
_CHUNK_RECORD = struct.Struct(">Q24sI")
_DEFAULT_CHUNK_BYTES: Final[int] = 1024 * 1024
_MASK32 = 0xFFFFFFFF


class CryptoError(RuntimeError):
    pass


@dataclass(frozen=True)
class SealedObjectInfo:
    key_id: str
    plaintext_bytes: int
    ciphertext_bytes: int
    algorithm: str = "XChaCha20-Poly1305"


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    if length <= 0 or length > 255 * hashlib.sha256().digest_size:
        raise ValueError("invalid HKDF output length")
    result = bytearray()
    previous = b""
    counter = 1
    while len(result) < length:
        previous = hmac.new(prk, previous + info + bytes((counter,)), hashlib.sha256).digest()
        result.extend(previous)
        counter += 1
    return bytes(result[:length])


def derive_key(master_key: bytes, *, project_id: str, key_id: str) -> bytes:
    if len(master_key) != _KEY_BYTES:
        raise CryptoError("master key must be exactly 32 bytes")
    salt = hashlib.sha256(("syntavra:" + project_id).encode("utf-8")).digest()
    prk = _hkdf_extract(salt, master_key)
    return _hkdf_expand(prk, ("evidence-xchacha20poly1305:" + key_id).encode("utf-8"), 32)


def _rotl32(value: int, shift: int) -> int:
    return ((value << shift) & _MASK32) | (value >> (32 - shift))


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & _MASK32
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 16)
    state[c] = (state[c] + state[d]) & _MASK32
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 12)
    state[a] = (state[a] + state[b]) & _MASK32
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 8)
    state[c] = (state[c] + state[d]) & _MASK32
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 7)


def _rounds(state: list[int]) -> None:
    for _ in range(10):
        _quarter_round(state, 0, 4, 8, 12)
        _quarter_round(state, 1, 5, 9, 13)
        _quarter_round(state, 2, 6, 10, 14)
        _quarter_round(state, 3, 7, 11, 15)
        _quarter_round(state, 0, 5, 10, 15)
        _quarter_round(state, 1, 6, 11, 12)
        _quarter_round(state, 2, 7, 8, 13)
        _quarter_round(state, 3, 4, 9, 14)


def _words(data: bytes) -> list[int]:
    return list(struct.unpack("<" + "I" * (len(data) // 4), data))


def _hchacha20(key: bytes, nonce16: bytes) -> bytes:
    if len(key) != 32 or len(nonce16) != 16:
        raise CryptoError("invalid HChaCha20 input length")
    constants = _words(b"expand 32-byte k")
    state = constants + _words(key) + _words(nonce16)
    working = state.copy()
    _rounds(working)
    return struct.pack("<8I", working[0], working[1], working[2], working[3], working[12], working[13], working[14], working[15])


def _chacha20_block(key: bytes, counter: int, nonce12: bytes) -> bytes:
    if len(key) != 32 or len(nonce12) != 12 or not 0 <= counter <= _MASK32:
        raise CryptoError("invalid ChaCha20 block parameters")
    state = _words(b"expand 32-byte k") + _words(key) + [counter] + _words(nonce12)
    working = state.copy()
    _rounds(working)
    output = [(working[index] + state[index]) & _MASK32 for index in range(16)]
    return struct.pack("<16I", *output)


def _chacha20_xor(key: bytes, nonce12: bytes, counter: int, data: bytes) -> bytes:
    output = bytearray(len(data))
    position = 0
    current = counter
    while position < len(data):
        if current > _MASK32:
            raise CryptoError("ChaCha20 counter exhausted")
        block = _chacha20_block(key, current, nonce12)
        take = min(64, len(data) - position)
        for index in range(take):
            output[position + index] = data[position + index] ^ block[index]
        position += take
        current += 1
    return bytes(output)


def _poly1305(message: bytes, one_time_key: bytes) -> bytes:
    if len(one_time_key) != 32:
        raise CryptoError("Poly1305 key must be 32 bytes")
    r = int.from_bytes(one_time_key[:16], "little")
    r &= 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(one_time_key[16:], "little")
    accumulator = 0
    prime = (1 << 130) - 5
    for offset in range(0, len(message), 16):
        block = message[offset:offset + 16]
        value = int.from_bytes(block + b"\x01", "little")
        accumulator = ((accumulator + value) * r) % prime
    return ((accumulator + s) % (1 << 128)).to_bytes(16, "little")


def _pad16(value: bytes) -> bytes:
    return b"" if len(value) % 16 == 0 else b"\x00" * (16 - len(value) % 16)


def _aead_material(key: bytes, nonce24: bytes) -> tuple[bytes, bytes]:
    if len(nonce24) != 24:
        raise CryptoError("XChaCha20 nonce must be 24 bytes")
    subkey = _hchacha20(key, nonce24[:16])
    nonce12 = b"\x00\x00\x00\x00" + nonce24[16:]
    return subkey, nonce12


def _aead_seal(key: bytes, nonce24: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    subkey, nonce12 = _aead_material(key, nonce24)
    poly_key = _chacha20_block(subkey, 0, nonce12)[:32]
    ciphertext = _chacha20_xor(subkey, nonce12, 1, plaintext)
    mac_data = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) + struct.pack("<QQ", len(aad), len(ciphertext))
    return ciphertext, _poly1305(mac_data, poly_key)


def _aead_open(key: bytes, nonce24: bytes, ciphertext: bytes, tag: bytes, aad: bytes) -> bytes:
    if len(tag) != _TAG_BYTES:
        raise CryptoError("authentication tag is truncated")
    subkey, nonce12 = _aead_material(key, nonce24)
    poly_key = _chacha20_block(subkey, 0, nonce12)[:32]
    mac_data = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) + struct.pack("<QQ", len(aad), len(ciphertext))
    expected = _poly1305(mac_data, poly_key)
    if not hmac.compare_digest(tag, expected):
        raise CryptoError("sealed object authentication failed")
    return _chacha20_xor(subkey, nonce12, 1, ciphertext)


def seal(plaintext: bytes, *, master_key: bytes, project_id: str, key_id: str) -> bytes:
    encoded_key_id = key_id.encode("utf-8")
    if not encoded_key_id or len(encoded_key_id) > 255:
        raise CryptoError("key_id must encode to 1..255 bytes")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    key = derive_key(master_key, project_id=project_id, key_id=key_id)
    header = _HEADER.pack(_MAGIC, len(encoded_key_id), len(nonce), len(plaintext))
    aad = header + encoded_key_id + nonce
    ciphertext, tag = _aead_seal(key, nonce, plaintext, aad)
    return aad + ciphertext + tag


def inspect_sealed(data: bytes) -> SealedObjectInfo:
    if len(data) < _HEADER.size + 1 + _NONCE_BYTES + _TAG_BYTES:
        raise CryptoError("sealed object is truncated")
    magic, key_id_size, nonce_size, plaintext_size = _HEADER.unpack(data[:_HEADER.size])
    if magic != _MAGIC or nonce_size != _NONCE_BYTES or key_id_size < 1:
        raise CryptoError("invalid sealed object header")
    end_key = _HEADER.size + key_id_size
    end_nonce = end_key + nonce_size
    if len(data) != end_nonce + plaintext_size + _TAG_BYTES:
        raise CryptoError("sealed object length mismatch")
    try:
        key_id = data[_HEADER.size:end_key].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CryptoError("sealed key id is invalid UTF-8") from exc
    return SealedObjectInfo(key_id, int(plaintext_size), len(data))


def open_sealed(data: bytes, *, master_key: bytes, project_id: str) -> tuple[bytes, SealedObjectInfo]:
    info = inspect_sealed(data)
    _, key_id_size, nonce_size, plaintext_size = _HEADER.unpack(data[:_HEADER.size])
    end_key = _HEADER.size + key_id_size
    end_nonce = end_key + nonce_size
    end_ciphertext = end_nonce + plaintext_size
    nonce = data[end_key:end_nonce]
    key = derive_key(master_key, project_id=project_id, key_id=info.key_id)
    plaintext = _aead_open(key, nonce, data[end_nonce:end_ciphertext], data[end_ciphertext:], data[:end_nonce])
    return plaintext, info


class KeyRing:
    """Environment- or project-key-backed evidence key ring.

    Managed deployments should set SYNTAVRA_EVIDENCE_MASTER_KEY_B64 and disable
    local keys. Local development defaults to a private 0600 project key.
    """

    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        env_name: str = "SYNTAVRA_EVIDENCE_MASTER_KEY_B64",
        allow_local_key: bool = True,
    ):
        self.root = Path(root)
        self.project_id = project_id
        self.env_name = env_name
        self.allow_local_key = bool(allow_local_key)
        self.keys = self.root / "keys"
        self.registry_path = self.keys / "registry.json"
        self.keys.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _decode_environment(value: str) -> bytes:
        try:
            raw = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise CryptoError("invalid base64 evidence master key") from exc
        if len(raw) != _KEY_BYTES:
            raise CryptoError("environment evidence master key must decode to 32 bytes")
        return raw

    def active(self) -> tuple[str, bytes, str]:
        value = os.environ.get(self.env_name, "")
        if value:
            return "env-v1", self._decode_environment(value), "environment"
        if not self.allow_local_key:
            raise CryptoError(f"managed evidence encryption requires {self.env_name}")
        registry = self._load_registry()
        key_id = str(registry.get("active") or "local-v1")
        key_path = self.keys / f"{key_id}.key"
        if not key_path.exists():
            self._write_private(key_path, secrets.token_bytes(_KEY_BYTES))
            registry = {"schema_version": 1, "active": key_id, "keys": [key_id]}
            self._write_private(self.registry_path, json.dumps(registry, sort_keys=True).encode("utf-8"))
        raw = key_path.read_bytes()
        if len(raw) != _KEY_BYTES:
            raise CryptoError("local evidence master key has invalid length")
        return key_id, raw, "local-file"

    def get(self, key_id: str) -> bytes:
        if key_id == "env-v1":
            value = os.environ.get(self.env_name, "")
            if not value:
                raise CryptoError(f"missing {self.env_name} required to decrypt evidence")
            return self._decode_environment(value)
        path = self.keys / f"{key_id}.key"
        if not path.is_file():
            raise CryptoError(f"evidence key is unavailable: {key_id}")
        raw = path.read_bytes()
        if len(raw) != _KEY_BYTES:
            raise CryptoError("evidence key has invalid length")
        return raw

    def rotate(self) -> str:
        if os.environ.get(self.env_name):
            raise CryptoError("environment-managed keys must be rotated outside Syntavra")
        registry = self._load_registry()
        next_index = len(registry.get("keys") or []) + 1
        key_id = f"local-v{next_index}"
        self._write_private(self.keys / f"{key_id}.key", secrets.token_bytes(_KEY_BYTES))
        keys = list(dict.fromkeys([*(registry.get("keys") or []), key_id]))
        self._write_private(
            self.registry_path,
            json.dumps({"schema_version": 1, "active": key_id, "keys": keys}, sort_keys=True).encode("utf-8"),
        )
        return key_id

    def _load_registry(self) -> dict[str, object]:
        if not self.registry_path.is_file():
            return {"schema_version": 1, "active": "local-v1", "keys": ["local-v1"]}
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CryptoError("evidence key registry is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise CryptoError("unsupported evidence key registry")
        return value

    @staticmethod
    def _write_private(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def seal_file(
    source: Path,
    destination: Path,
    *,
    master_key: bytes,
    project_id: str,
    key_id: str,
    chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
) -> SealedObjectInfo:
    if chunk_bytes < 64 * 1024 or chunk_bytes > 64 * 1024 * 1024:
        raise CryptoError("chunk_bytes must be between 64 KiB and 64 MiB")
    size = source.stat().st_size
    count = (size + chunk_bytes - 1) // chunk_bytes if size else 0
    encoded_key_id = key_id.encode("utf-8")
    if not encoded_key_id or len(encoded_key_id) > 255:
        raise CryptoError("invalid key id")
    header = _CHUNK_HEADER.pack(_CHUNK_MAGIC, len(encoded_key_id), chunk_bytes, size, count)
    key = derive_key(master_key, project_id=project_id, key_id=key_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        output_handle.write(header)
        output_handle.write(encoded_key_id)
        for index in range(count):
            plaintext = input_handle.read(chunk_bytes)
            nonce = secrets.token_bytes(_NONCE_BYTES)
            record = _CHUNK_RECORD.pack(index, nonce, len(plaintext))
            aad = header + encoded_key_id + record
            ciphertext, tag = _aead_seal(key, nonce, plaintext, aad)
            output_handle.write(record)
            output_handle.write(ciphertext)
            output_handle.write(tag)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return SealedObjectInfo(key_id, size, destination.stat().st_size)


def inspect_sealed_file(source: Path) -> SealedObjectInfo:
    with source.open("rb") as handle:
        header = handle.read(_CHUNK_HEADER.size)
        if len(header) != _CHUNK_HEADER.size:
            raise CryptoError("sealed file header is truncated")
        magic, key_size, chunk_bytes, plaintext_size, count = _CHUNK_HEADER.unpack(header)
        if magic != _CHUNK_MAGIC or key_size < 1 or chunk_bytes < 1:
            raise CryptoError("invalid sealed file header")
        encoded_key = handle.read(key_size)
        try:
            key_id = encoded_key.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CryptoError("invalid sealed file key id") from exc
        seen = 0
        for expected_index in range(count):
            record = handle.read(_CHUNK_RECORD.size)
            if len(record) != _CHUNK_RECORD.size:
                raise CryptoError("sealed file record is truncated")
            index, nonce, length = _CHUNK_RECORD.unpack(record)
            if index != expected_index or len(nonce) != _NONCE_BYTES or length > chunk_bytes:
                raise CryptoError("invalid sealed file chunk record")
            if len(handle.read(length)) != length or len(handle.read(_TAG_BYTES)) != _TAG_BYTES:
                raise CryptoError("sealed file chunk is truncated")
            seen += length
        if seen != plaintext_size or handle.read(1):
            raise CryptoError("sealed file length mismatch")
    return SealedObjectInfo(key_id, int(plaintext_size), source.stat().st_size)


def open_sealed_file(
    source: Path,
    destination: Path,
    *,
    master_key: bytes,
    project_id: str,
) -> SealedObjectInfo:
    with source.open("rb") as input_handle:
        header = input_handle.read(_CHUNK_HEADER.size)
        if len(header) != _CHUNK_HEADER.size:
            raise CryptoError("sealed file header is truncated")
        magic, key_size, chunk_bytes, plaintext_size, count = _CHUNK_HEADER.unpack(header)
        if magic != _CHUNK_MAGIC or key_size < 1 or chunk_bytes < 1:
            raise CryptoError("invalid sealed file header")
        encoded_key_id = input_handle.read(key_size)
        try:
            key_id = encoded_key_id.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CryptoError("invalid sealed file key id") from exc
        key = derive_key(master_key, project_id=project_id, key_id=key_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output_handle:
            seen = 0
            for expected_index in range(count):
                record = input_handle.read(_CHUNK_RECORD.size)
                if len(record) != _CHUNK_RECORD.size:
                    raise CryptoError("sealed file record is truncated")
                index, nonce, length = _CHUNK_RECORD.unpack(record)
                if index != expected_index or length > chunk_bytes:
                    raise CryptoError("invalid sealed file chunk record")
                ciphertext = input_handle.read(length)
                tag = input_handle.read(_TAG_BYTES)
                if len(ciphertext) != length or len(tag) != _TAG_BYTES:
                    raise CryptoError("sealed file chunk is truncated")
                aad = header + encoded_key_id + record
                plaintext = _aead_open(key, nonce, ciphertext, tag, aad)
                output_handle.write(plaintext)
                seen += len(plaintext)
            if seen != plaintext_size or input_handle.read(1):
                raise CryptoError("sealed file length mismatch")
            output_handle.flush()
            os.fsync(output_handle.fileno())
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return SealedObjectInfo(key_id, int(plaintext_size), source.stat().st_size)
