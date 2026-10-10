# -*- coding: utf-8 -*-
"""
run.py - 启动服务（start_server.bat / start_mock.bat 调用它）

同时开两个端口，模型只加载一次：
  http://IP:8000    普通访问；眼镜用这个
  https://IP:8443   手机用这个才能「按住提问」（浏览器规定：只有 HTTPS 页面能用麦克风）

HTTPS 证书是自己生成的（自签名），手机第一次打开会提示「不安全/隐私设置错误」，
点「高级 → 继续访问」即可。这是局域网自用，没有安全问题。
"""
import os, sys, socket, asyncio, datetime, ipaddress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

HTTP_PORT = int(os.environ.get("MUSEUM_PORT", "8000"))
HTTPS_PORT = int(os.environ.get("MUSEUM_HTTPS_PORT", "8443"))


def local_ips():
    ips = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:                                    # 找出真正用来上网的那块网卡
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80)); ips.add(s.getsockname()[0]); s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def ensure_cert(ips):
    """生成/复用自签名证书。IP 变了（换了 Wi-Fi）会自动重新生成。"""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("⚠️ 没装 cryptography，不开 HTTPS（手机不能语音提问）：pip install cryptography")
        return None
    d = config._writable_dir(config.SERVER_DIR / "certs", "certs")
    key_f, cert_f, ip_f = d / "key.pem", d / "cert.pem", d / "ips.txt"
    want = ",".join(ips)
    if key_f.exists() and cert_f.exists() and ip_f.exists() and ip_f.read_text() == want:
        return str(key_f), str(cert_f)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MuseumGlasses")])
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    san += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=800))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256()))
    key_f.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    cert_f.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    ip_f.write_text(want)
    return str(key_f), str(cert_f)


async def serve():
    import uvicorn
    import main                                       # 这一步加载所有模型
    ips = local_ips()
    cert = ensure_cert(ips)
    servers = [uvicorn.Server(uvicorn.Config(main.app, host="0.0.0.0", port=HTTP_PORT, log_level="warning"))]
    if cert:
        servers.append(uvicorn.Server(uvicorn.Config(main.app, host="0.0.0.0", port=HTTPS_PORT, log_level="warning",
                                                     ssl_keyfile=cert[0], ssl_certfile=cert[1])))
    print("\n" + "=" * 60)
    print(f"  电脑上打开：    http://localhost:{HTTP_PORT}")
    for ip in ips:
        if cert:
            print(f"  手机上打开：    https://{ip}:{HTTPS_PORT}   （提示不安全 → 高级 → 继续访问）")
        print(f"  眼镜 SERVER=   http://{ip}:{HTTP_PORT}")
    print("  手机/眼镜必须和这台电脑连同一个 Wi-Fi 或热点")
    print("=" * 60 + "\n服务已启动，关闭本窗口即停止。\n")
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
