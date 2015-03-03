"""
Ecnspider2: Qofspider-based tool for measuring ECN-linked connectivity
Derived from ECN Spider (c) 2014 Damiano Boppart <hat.guy.repo@gmail.com>

.. moduleauthor:: Brian Trammell <brian@trammell.ch>

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, write to the Free Software Foundation, Inc.,
    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""

import qofspider
import http.client
import collections

# Flags constants
TCP_CWR = 0x80
TCP_ECE = 0x40
TCP_URG = 0x20
TCP_ACK = 0x10
TCP_PSH = 0x08
TCP_RST = 0x04
TCP_SYN = 0x02
TCP_FIN = 0x01

# QoF TCP Characteristics constants
QOF_SYNECT0 =    0x0100
QOF_SYNECT1 =    0x0200
QOF_SYNCE   =    0x0400
QOF_SYNTSOPT =   0x1000
QOF_SYNSACKOPT = 0x2000
QOF_SYNWSOPT =   0x4000
QOF_ECT0 =       0x01
QOF_ECT1 =       0x02
QOF_CE   =       0x04
QOF_TSOPT =      0x10
QOF_SACKOPT =    0x20
QOF_WSOPT =      0x40

Connection = collections.namedtuple("Connection",["client","port","state"])
Connection.OK = 0
Connection.FAILED = 1
Connection.TIMEOUT = 2

SpiderRecord = collections.namedtuple("SpiderRecord",
    ["ip","host","port","ecnstate","connstate","httpstatus"])

FlowRecord = collections.namedtuple("FlowRecord",
    ["ip","port","octets","fif","fsf","fuf","fir","fsr","fur"])

MergedRecord = collections.namedtuple("FlowRecord",
    ["ip","host","ecnstate","connstate","httpstatus",
     "octets","fif","fsf","fuf","fir","fsr","fur"])

class EcnSpider2(QofSpider):
    def __init__(self, result_sink,
                 local_ip4, local_ip6, 
                 worker_count, conn_timeout, 
                 interface_uri, qof_port=4739):
        super().__init__(worker_count, interface_uri, qof_port)

        self.conn_timeout = conn_timeout
        self.local_ip4 = local_ip4
        self.local_ip6 = local_ip6
        self.result_sink = result_sink

    def connect(self, job, pcs, config):
        client = http.client.HTTPConnection(job.ip, timeout=self.conn_imeout)
        client.auto_open = 0
        try:
            client.connect()
        except socket.timeout:
            return Connection(None, None, Connection.TIMEOUT)
        except OSError as e:
            return Connection(None, None, Connection.FAIL)
        else:
            return (client, client.sock.getsockname()[1], Connection.OK)

    def post_connect(self, job, conn, pcs, config):
        if conn.status == Connection.OK:
            headers = {'User-Agent': USER_AGENT, 
                       'Connection': 'close'
                       'Host': job.host}
            try:
                conn.client.request('GET', '/', headers=headers)
                res = conn.client.getresponse()
                conn.client.close()

                return SpiderRecord(job.ip, job.host, conn.port, config, True, res.status)
            except:
                return SpiderRecord(job.ip, job.host, conn.port, config, True, 0)
            finally:
                conn.client.close()
        else:
            return SpiderRecord(job.ip, job.host, 0, config, False, 0)

    def qof_config(self):
        return { 'template' : [
                 'flowStartMilliseconds',
                 'flowEndMilliseconds',
                 'octetDeltaCount',
                 'reverseOctetDeltaCount',
                 'packetDeltaCount',
                 'reversePacketDeltaCount',
                 'transportOctetDeltaCount',
                 'reverseTransportOctetDeltaCount',
                 'transportPacketDeltaCount',
                 'reverseTransportPacketDeltaCount',
                 'sourceIPv4Address',
                 'destinationIPv4Address',
                 'sourceIPv6Address',
                 'destinationIPv6Address',
                 'sourceTransportPort',
                 'destinationTransportPort',
                 'protocolIdentifier',
                 'initialTCPFlags',
                 'reverseInitialTCPFlags',
                 'unionTCPFlags',
                 'reverseUnionTCPFlags',
                 'lastSynTcpFlags',
                 'reverseLastSynTcpFlags',
                 'qofTcpCharacteristics',
                 'reverseQofTcpCharacteristics']}

    def tupleize_flow(self, flow):
        # Short-circuit non-HTTP over TCP flows, and reset storms
        try:
            if flow["protocolIdentifier"] != 6:
                return None
            if flow["destinationTransportPort"] != 80:
                return None
            if flow["initialTCPFlags"] | TCP_RST:
                return None
        except:
            return None

        # Short-circuit flows not from this source,
        # and select destination address based on version
        if ("sourceIPv4Address" in flow and
          flow["sourceIPv4Address"] == self.local_ip4):
            ip = flow["destinationIPv4Address"]
        elif "sourceIPv6Address" in flow and
          flow["sourceIPv6Address"] == self.local_ip6):
            ip = flow["destinationIPv6Address"]
        else:
            return None

        # Merge flags
        fif = flow["initialTCPFlags"]
        fsf = (flow["lastSynTCPFlags"] |
              (flow["qofTcpCharacteristics"] & 0xFF00))
        fuf = (flow["unionTCPFlags"] |
              ((flow["qofTcpCharacteristics"] & 0xFF) << 8))
        fir = flow["reverseInitialTCPFlags"]
        fsr = (flow["reverseLastSynTCPFlags"] |
              (flow["reverseQofTcpCharacteristics"] & 0xFF00))
        fur = (flow["reverseUnionTCPFlags"] |
              ((flow["reverseQofTcpCharacteristics"] & 0xFF) << 8))

        # Export record
        return FlowRecord(ip, 
                          flow["sourceTransportPort"],
                          flow["reverseTransportOctetDeltaCount"],
                          fif, fsf, fuf, fir, fsr, fur)

    def merge(self, flow, res):
        self.result_sink(MergedRecord(res.ip, res.host, 
                res.ecnstate, res.connstate, res.httpstatus,
                flow.octets, flow.fif, flow.fsf, flow.fuf,
                flow.fir, flow.fsr, flow.fur))

class EcnSpider2Linux(EcnSpider2):

    def config_zero(self):
        subprocess.check_call(['sudo', '-n', '/sbin/sysctl', '-w', 'net.ipv4.tcp_ecn=2'])

    def config_one(self):
        subprocess.check_call(['sudo', '-n', '/sbin/sysctl', '-w', 'net.ipv4.tcp_ecn=1'])

    def __init__(self, result_sink,
                 local_ip4, local_ip6, 
                 worker_count, conn_timeout, 
                 interface_uri, qof_port=4739):
        super().__init__(result_sink, local_ip4, local_ip6, 
                         worker_count, conn_timeout, interface_uri, qof_port)

def main():
    pass

if __name__ == "__main__":
    main()