import numpy as np

def parse_cap20(data: bytes, n: int) -> np.ndarray:
    """5 байт → 2 комплексных сэмпла."""
    out = np.zeros(n, dtype=np.complex64)
    j, idx = 0, 0
    while j < n and idx + 5 <= len(data):
        b0, b1, b2, b3, b4 = data[idx:idx+5]
        a = b0 | (b1 << 8) | ((b2 & 0x0F) << 16)
        b = ((b2 >> 4) & 0x0F) | (b3 << 4) | (b4 << 12)
        for k, w in enumerate((a, b)):
            if j + k >= n: break
            i_val = w & 0x3FF
            q_val = (w >> 10) & 0x3FF
            # two's complement 10 → 16 бит
            if i_val & 0x200: i_val -= 0x400
            if q_val & 0x200: q_val -= 0x400
            out[j + k] = i_val + 1j * q_val
        idx += 5
        j += 2
    return out


with open("iq.bin", "rb") as f:
    raw = f.read()

print(f"Файл: {len(raw)} байт, ожидаем {(8192 * 20 + 7)//8}")

iq = parse_cap20(raw, 8192)
print(f"Сэмплов: {len(iq)}")
print(f"IQ[0:5]: {iq[:5]}")

# Быстрый спектр всего буфера
spec = np.fft.fftshift(np.fft.fft(iq))
spec_db = 20 * np.log10(np.abs(spec) + 1e-12)

fs = 80e6  # 80 MS/s при rate=0
freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1/fs)) / 1e6  # МГц

import matplotlib.pyplot as plt
plt.figure(figsize=(12, 5))
plt.plot(freqs, spec_db)
plt.xlabel("Частота относительно центра, МГц")
plt.ylabel("dB")
plt.title("Спектр I/Q (2412 МГц)")
plt.grid(True)
plt.tight_layout()
plt.show()
