"""Exec FFmpeg with a kernel-enforced lifetime tied to the Cast worker.

Use a separate interpreter rather than preexec_fn in the multithreaded worker.
SIGKILL also terminates an encoder suspended by the buffering governor.
"""
import ctypes
import os
import signal
import sys


def main():
    parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), 'Cannot bind encoder lifetime to worker')
    if os.getppid() != parent:
        return  # Parent exited before the death signal was installed.
    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    main()
