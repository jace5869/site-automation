import socket, os, sys
pidfile = sys.argv[2]
open(pidfile, 'w').write(str(os.getpid()))
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen(5)
while True:
    c, _ = s.accept(); c.close()
