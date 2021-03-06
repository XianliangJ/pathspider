
import argparse
import csv
import logging
import time
import threading
import json

from straight.plugin import load

from pathspider.base import Spider
from pathspider.base import SHUTDOWN_SENTINEL

import sys

plugins = load("pathspider.plugins", subclasses=Spider)

print(repr(list(plugins)))

def job_feeder(inputfile, spider):
    with open(inputfile) as fp:
        print("job_feeder: started")
        reader = csv.reader(fp, delimiter=',', quotechar='"')
        for row in reader:
            # port numbers should be integers
            row[1] = int(row[1])

            spider.add_job(row)
        
        print("job_feeder: all jobs added, waiting for spider to finish")
        spider.shutdown()
        print("job_feeder: stopped")

def run_pathspider():
    parser = argparse.ArgumentParser(description='''Pathspider will spider the
            paths.''')
    parser.add_argument('-s', '--standalone', action='store_true', help='''run in
        standalone mode. this is the default mode (and currently the only supported
        mode). in the future, mplane will be supported as a mode of operation.''')
    parser.add_argument('-l', '--list-plugins', action='store_true',
            help='''print the list of installed plugins''')
    parser.add_argument('-p', '--plugin', help='''use named plugin''')
    parser.add_argument('-i', '--interface', help='''the interface to use for the observer''')
    parser.add_argument('-w', '--worker-count', type=int, help='''number of workers to use''')
    parser.add_argument('-I', '--input-file', metavar='INPUTFILE', help='''a file
            containing a list of remote hosts to test, with any accompanying
            metadata expected by the pathspider test. this file should be formatted
            as a comma-seperated values file.''')
    parser.add_argument('-o', '--output-file', metavar='OUTPUTFILE', help='''the file to output results data to''')

    args = parser.parse_args()

    logging.basicConfig()
    logging.getLogger().setLevel(logging.INFO)
    logger = logging.getLogger("pathspider")

    if args.list_plugins:
        print("The following plugins are available:\n")
        for plugin in plugins:
            print(" * " + plugin.__name__)
        print("\nSpider safely!")
        sys.exit(0)

    try:
        # set some defaults
        selected = args.plugin or "ECNSpider"
        worker_count = args.worker_count or 100
        interface = args.interface or "eth0"

        spider = None
        for plugin in plugins:
            if plugin.__name__ == selected:
                spider = plugin(worker_count, "int:" + interface)
        if spider == None:
            logger.error("Plugin not found! Cannot continue.")
            logger.error("Use -l to list all plugins.")
            sys.exit(1)
        
        print("activating spider...")
        
        spider.start()

        print("starting to add jobs")
        threading.Thread(target=job_feeder, args=(args.input_file, spider)).start()
        
        with open(args.output_file, 'w') as outputfile:
            while True:
                result = spider.outqueue.get()
                if result == SHUTDOWN_SENTINEL:
                    break
                outputfile.write(json.dumps(result) + "\n")
                spider.outqueue.task_done()

    except KeyboardInterrupt:
        print("kthxbye")

if __name__ == "__main__":
    run_pathspider()
