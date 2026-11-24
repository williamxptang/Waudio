"""Messages between the engine and a model worker, over the worker's stdin and stdout.

One message: a 4-byte big-endian length, a JSON header of that many bytes, then the arrays the
header lists ("arrays": [{"dtype": "<f4", "shape": [n]}, …]) as raw bytes, one after another.
JSON and plain bytes only, no pickle: a worker written in another language can speak it.

engine -> worker                               worker -> engine
  {"op": "start", "kind": "asr" | "tts", …}      {"op": "ready", "info": {…}}
  {"op": "call", "method": …, "args": {…}}       {"op": "result", "value": …} + arrays
  {"op": "reply", "value": …} / "error"          {"op": "callback", "name": …, "args": {…}} + arrays
  {"op": "quit"}                                 {"op": "error", "type": …, "message": …}

A callback (should_stop, on_chunk) is the worker asking the engine mid-call; it waits for the reply.
"""
import json, struct

import numpy as np


def send(stream, header, arrays=()):
    arrays = [np.ascontiguousarray(a) for a in arrays]
    head = json.dumps({**header, "arrays": [{"dtype": a.dtype.str, "shape": list(a.shape)} for a in arrays]}).encode()
    stream.write(struct.pack(">I", len(head)) + head)
    for a in arrays:
        stream.write(a.tobytes())
    stream.flush()


def recv(stream):
    """-> (header, arrays), or None when the stream has ended between messages."""
    size = _read(stream, 4, at_start=True)
    if size is None:
        return None
    header = json.loads(_read(stream, struct.unpack(">I", size)[0]))
    arrays = []
    for spec in header.pop("arrays", []):
        dtype, shape = np.dtype(spec["dtype"]), [int(n) for n in spec["shape"]]
        data = _read(stream, dtype.itemsize * int(np.prod(shape, dtype=np.int64)))
        arrays.append(np.frombuffer(data, dtype).reshape(shape).copy())
    return header, arrays


def _read(stream, n, at_start=False):
    chunks, left = [], n
    while left:
        chunk = stream.read(left)
        if not chunk:
            if at_start and left == n:
                return None
            raise EOFError("the stream ended inside a message")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)
