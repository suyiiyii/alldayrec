"""python -m alldayrec, the alldayrec command: the server (server.main), or with --infer the inference service
(infer_server.main, for INFER_URL). --infer is told apart before either is imported: the service never loads the
server's modules (database, routes, pipelines)."""
import sys


def main():
    if "--infer" in sys.argv[1:]:
        sys.argv.remove("--infer")
        from alldayrec.infer_server import main as run
    else:
        from alldayrec.server import main as run
    run()


if __name__ == "__main__":
    main()
