"""Model workers: each model runs in a process of its own that exits when the model is unloaded, so
the engine never keeps what a model library leaves behind (see __main__.py, client.py, remote.py)."""
