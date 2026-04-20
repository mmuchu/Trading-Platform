import zlib
lines = open('v31.b64').read().split('\n')
print(f'lines={len(lines)}')
good = [
    (7752, '0xc805ac56'),
    (7752, '0xcec4832c'),
    (7752, '0xa24477ec'),
    (7752, '0x9af4c860'),
    (7752, '0x4b90bea3'),
    (7748, '0xf9816a3d'),
]
for c in range(6):
    s = lines[c*102:(c+1)*102]
    data = ''.join(l.strip() for l in s if l.strip())
    crc = f'{zlib.crc32(data.encode()):#010x}'
    status = 'OK' if len(data) == good[c][0] and crc == good[c][1] else 'BAD'
    print(f'Chunk {c+1}: len={len(data)} crc={crc} expected={good[c][1]} {status}')