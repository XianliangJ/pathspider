
import sys
import logging
import subprocess
import traceback

import socket
import collections

from pathspider.base import Spider
from pathspider.base import NO_FLOW

from pathspider.observer import Observer
from pathspider.observer import basic_flow
from pathspider.observer import basic_count

Connection = collections.namedtuple("Connection", ["client", "port", "state"])
SpiderRecord = collections.namedtuple("SpiderRecord", ["ip", "rport", "port",
                                                       "host", "tfostate",
                                                       "connstate", "rank"])

CONN_OK = 0
CONN_FAILED = 1
CONN_TIMEOUT = 2

USER_AGENT = "pathspider"

## Chain functions

def tcpcompleted(rec, tcp, rev): # pylint: disable=W0612,W0613
    return not tcp.fin_flag

def tfosetup(rec, ip):
    rec['tfo_seq'] = -1000
    rec['tfo_len'] = -1000
    rec['tfoworking'] = 0
    return True

def _tfocookie(tcp):
    """
    Determine whether a TCP header (from python-libtrace) contains a TFO cookie or not.
    """

    options = tcp.data[20:tcp.doff*4]
    cp = 0

    while True:
        if not options:
            return None
        if (options[cp] == 0):
            return None
        if options[cp] == 1:
            cp += 1
            continue
        if options[cp] == 34:
            # IANA-allocated option
            if options[cp+1] == 2:
                cp += 2
                continue
            else:
                return (0,34)
        if options[cp] == 253:
            # Experimental option 253
            if (options[cp+2], options[cp+3]) == (249, 137):
                if options[cp+1] <= 4:
                    cp += options[cp+1]
                    continue
                else:
                    return (0,253)
        if options[0] == 254:
            # Experimental option 254
            if (options[cp+2], options[cp+3]) == (249, 137):
                if options[cp+1] <= 4:
                    options = options[cp+1]
                    continue
                else:
                    return (0,254)
        else:
            if options[cp+1] <= len(options):
                return False
            else:
                cp += options[cp+1]
                continue
    return False

def tfoworking(rec, tcp, rev):
    
    outgoing = (rec['sp'] == tcp.src_port)
    data_len = (len(tcp.data) - tcp.doff*4)
    has_cookie = bool(_tfocookie(tcp))
    has_data = (data_len > 0)
    
    if ((rec['tfo_seq'] < 0) & (not has_cookie)):#no TFO data sent or received
        return True
    
    if (has_cookie & has_data & outgoing):#TFO data sent
        rec['tfo_seq'] = tcp.seq_nbr
        rec['tfo_len'] = data_len
        rec['tfoworking'] = 1
        return True
       
    if ((not outgoing) & (rec['tfo_seq'] > 0) & (tcp.ack_nbr == rec['tfo_seq'] + rec['tfo_len'] + 1)):#TFO acknowledged
        rec['tfoworking'] = 2
        return False
    
    if (outgoing & has_data & (tcp.seq_nbr == rec['tfo_seq'] + 1)):#TCP fall back, retransmission
        rec['tfo_seq'] = -500
        rec['tfo_len'] = -500
        return True
    
    return True

def test_tfocookie(fn=_tfocookie):
    """
    Test the _tfocookie() options parser on a static packet dump test file.
    This is used mainly for performance evaluation of the parser for now,
    and does not check for correctness.

    """
    import plt as libtrace

    lturi = "pcapfile:testdata/tfocookie.pcap"
    trace = libtrace.trace(lturi)
    trace.start()
    pkt = libtrace.packet()
    cookies = 0
    nocookies = 0

    while trace.read_packet(pkt):
        if not pkt.tcp:
            continue

        # just do the parse
        if fn(pkt.tcp):
            cookies += 1
        else:
            nocookies += 1

    print("cookies: %u, nocookies: %u" % (cookies, nocookies))


## TFOSpider main class

class TFOSpider(Spider):

    def __init__(self, worker_count, libtrace_uri, check_interrupt=None):
        super().__init__(worker_count=worker_count,
                         libtrace_uri=libtrace_uri)
        self.tos = None # set by configurator
        self.conn_timeout = 10

    def config_zero(self):
        pass

    def config_one(self):
        pass
        
    def connect(self, job, pcs, config):
        # determine ip version
        if job[0].count(':') >= 1: 
            af = socket.AF_INET6
        else: 
            af = socket.AF_INET
        
        # regular TCP
        if config == 0:
            sock = socket.socket(af, socket.SOCK_STREAM)

            try:
                sock.settimeout(self.conn_timeout)
                sock.connect((job[0], job[1]))

                return Connection(sock, sock.getsockname()[1], CONN_OK)             
            except TimeoutError:
                return Connection(sock, sock.getsockname()[1], CONN_TIMEOUT)
            except OSError:
                return Connection(sock, sock.getsockname()[1], CONN_FAILED)    
        
        # with TFO
        if config == 1:
            message = bytes("GET / HTTP/1.1\r\nhost: "+str(job[2])+"\r\n\r\n", "utf-8")
            
            # step one: request cookie
            try:
                sock = socket.socket(af, socket.SOCK_STREAM)
                sock.sendto(message, socket.MSG_FASTOPEN, (job[0], job[1]))
                sock.close()
            except:
                pass
            
            # step two: use cookie
            try:
                sock = socket.socket(af, socket.SOCK_STREAM)
                sock.sendto(message, socket.MSG_FASTOPEN, (job[0], job[1]))
                
                return Connection(sock, sock.getsockname()[1], CONN_OK)
            except TimeoutError:
                return Connection(sock, sock.getsockname()[1], CONN_TIMEOUT)
            except OSError:
                return Connection(sock, sock.getsockname()[1], CONN_FAILED)  

    def post_connect(self, job, conn, pcs, config):
        if conn.state == CONN_OK:
            rec = SpiderRecord(job[0], job[1], conn.port, job[2], config, True, job[3])
        else:
            rec = SpiderRecord(job[0], job[1], conn.port, job[2], config, False, job[3])

        try:
            conn.client.shutdown(socket.SHUT_RDWR)
        except:
            pass

        try:
            conn.client.close()
        except:
            pass

        return rec

    def create_observer(self):
        logger = logging.getLogger('tfospider')
        logger.info("Creating observer")
        try:
            return Observer(self.libtrace_uri,
                            new_flow_chain=[basic_flow, tfosetup],
                            ip4_chain=[basic_count],
                            ip6_chain=[basic_count],
                            tcp_chain=[tfoworking, tcpcompleted])
        except:
            logger.error("Observer not cooperating, abandon ship")
            traceback.print_exc()
            sys.exit(-1)

    def merge(self, flow, res):
        logger = logging.getLogger('tfospider')
        if flow == NO_FLOW:
            flow = {"dip": res.ip, "sp": res.port, "dp": res.rport, "connstate": res.connstate, "tfostate": res.tfostate, "observed": False }
        else:
            flow['connstate'] = res.connstate
            flow['host'] = res.host
            flow['rank'] = res.rank
            flow['tfostate'] = res.tfostate
            flow['observed'] = True
        
        logger.debug("Result: " + str(flow))
        self.outqueue.put(flow)

