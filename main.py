import serial, time, struct, re

PORT = 'COM8'
BAUD = 2000000
N = 8192                # сэмплов за один захват
RATE = 0         # 80 MS/s
FORMAT = 20             # CAP20 → 10 бит I + 10 бит Q

ser = serial.Serial(PORT, BAUD, timeout=2)
time.sleep(0.5)

def cmd(c, wait=0.2):
    ser.reset_input_buffer()
    ser.write((c + '\n').encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting)

# Настройка
print(cmd("FREQ 2412"))
print(cmd("GAIN MANUAL 40"))

# Запуск захвата
ser.reset_input_buffer()
ser.write(f"CAP20 {N} {RATE}\n".encode())

# Читаем заголовок до \n
header = b''
while not header.endswith(b'\n'):
    header += ser.read(1)
print("Header:", header)

m = re.match(rb'DATA (\d+) ([0-9a-fA-F]+) (\d+)\n', header)
n, crc, elapsed = int(m.group(1)), int(m.group(2), 16), int(m.group(3))
print(f"n={n}, crc=0x{crc:08x}, elapsed={elapsed}us")

# Читаем бинарные данные
total_bytes = (n * 20 + 7) // 8
data = b''
while len(data) < total_bytes:
    chunk = ser.read(total_bytes - len(data))
    if not chunk: break
    data += chunk
print(f"Получено {len(data)}/{total_bytes} байт")

with open("iq.bin", "wb") as f:
    f.write(data)
print("Сохранено в iq.bin")
ser.close()
