/// Explicit HTTP fixture bootstrap. A numeric loopback listener does not need
/// reverse DNS, which can block before a fixture publishes its readiness.
enum LoopbackHTTPFixture {
    static func script(_ body: String) -> String {
        #"""
import http.server as _jarvis_http
import socket as _jarvis_socket
import socketserver as _jarvis_socketserver

class LoopbackHTTPServer(_jarvis_http.ThreadingHTTPServer):
    def server_bind(self):
        if (self.address_family != _jarvis_socket.AF_INET
                or not isinstance(self.server_address, tuple)
                or len(self.server_address) != 2
                or self.server_address[0] != '127.0.0.1'
                or type(self.server_address[1]) is not int
                or not 0 <= self.server_address[1] <= 65535):
            raise ValueError('fixture_requires_numeric_ipv4_loopback')
        _jarvis_socketserver.TCPServer.server_bind(self)
        self.server_name = '127.0.0.1'
        self.server_port = self.server_address[1]

"""# + "\n" + body
    }
}
