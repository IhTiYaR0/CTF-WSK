import random
from datetime import datetime, timedelta

def generate_pro_ctf_logs(filename="soc_emergency.log", num_lines=45000):
    start_time = datetime(2026, 2, 8, 0, 0, 0)
    
    # Списки для разнообразия
    internal_ips = [f"10.10.5.{i}" for i in range(10, 255)]
    external_ips = [f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}" for _ in range(200)]
    
    methods = ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]
    resources = ["/index.php", "/api/v2/user", "/static/img/logo.png", "/login", "/search?q=", "/admin", "/config.php"]
    
    payloads = [
        "' OR 1=1 --", "<script>alert(1)</script>", "../../../etc/passwd", 
        "SELECT * FROM users", "/.env", "/.git/config", "() { :; }; /bin/bash"
    ]
    
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "sqlmap/1.7.2#stable", "Nikto/2.1.6", "curl/7.68.0", "Nmap Scripting Engine"
    ]

    with open(filename, "w") as f:
        target_port_count = 0
        
        for i in range(num_lines):
            curr_time = start_time + timedelta(seconds=i * 0.15 + random.random())
            ts = curr_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # Вероятность появления целевого порта 3426
            # Мы хотим около 200-250 записей
            if target_port_count < 220 and (random.random() < 0.006 or i > num_lines - 500):
                if random.random() < 0.5: # Имитируем активность в разные периоды
                    src = "10.10.5.42" # Скомпрометированный внутренний хост
                    dst = "193.16.4.1"  # IP хакера
                    port = 3426
                    status = "TUNNEL_OPEN"
                    info = f"DATA_EXFILTRATION_CHUNK_{target_port_count:03d}"
                    target_port_count += 1
                    f.write(f"{ts} | {src} -> {dst} | PORT: {port} | {status} | {info}\n")
                    continue

            # Генерируем "шум"
            dice = random.random()
            src = random.choice(external_ips)
            dst = "10.10.5.1"
            
            if dice < 0.1: # 10% - Агрессивные атаки (SQLi/LFI)
                port = 80
                status = "403_FORBIDDEN"
                info = f"{random.choice(methods)} {random.choice(resources)}{random.choice(payloads)}"
            elif dice < 0.2: # 10% - Брутфорс SSH
                port = 22
                status = "AUTH_FAILURE"
                info = f"User root failed from {src}"
            elif dice < 0.25: # 5% - Сканирование портов (рандомные порты)
                port = random.randint(1024, 65535)
                status = "REJECT"
                info = "Inbound connection attempt"
            else: # Остальное - нормальный трафик
                port = random.choice([80, 443])
                status = "200_OK"
                info = f"{random.choice(methods)} {random.choice(resources)} | {random.choice(user_agents)}"

            f.write(f"{ts} | {src} -> {dst} | PORT: {port} | {status} | {info}\n")

    print(f"✅ Логи готовы! Строк: {num_lines}. Порт 3426 встречается {target_port_count} раз.")

generate_pro_ctf_logs()