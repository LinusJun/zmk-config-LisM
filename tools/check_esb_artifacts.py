from pathlib import Path
import struct

def require(condition,message):
    if not condition: raise ValueError(message)

def check_radio_vectors(path):
    from elftools.elf.elffile import ELFFile
    with path.open('rb') as stream:
        elf = ELFFile(stream)
        symbols = elf.get_section_by_name('.symtab')
        table = symbols.get_symbol_by_name('_irq_vector_table')[0]
        section = elf.get_section(table['st_shndx'])
        offset = table['st_value'] - section['sh_addr']
        raw = section.data()
        for irq, handler in [(1,'mpsl_radio_isr_wrapper'), (8,'mpsl_timer0_isr_wrapper'),
                             (11,'mpsl_rtc0_isr_wrapper')]:
            address = struct.unpack_from('<I', raw, offset + 4*irq)[0]
            expected = symbols.get_symbol_by_name(handler)[0]['st_value'] | 1
            if address != expected:
                raise ValueError(f'IRQ {irq} is not owned by MPSL: {address:x} != {expected:x}')
        return {'flash_bytes':symbols.get_symbol_by_name('_flash_used')[0]['st_value'],
                'ram_bytes':symbols.get_symbol_by_name('_image_ram_size')[0]['st_value']}

def validate_uf2(data):
    require(data and len(data) % 512 == 0, "UF2 must contain complete 512-byte blocks")
    count = len(data) // 512
    payload = bytearray()
    for i in range(count):
        block = data[i * 512:(i + 1) * 512]
        magic, magic2, flags, address, size, index, total, family = struct.unpack("<8I", block[:32])
        require((magic, magic2, flags, size, index, total, family) ==
                (0x0A324655, 0x9E5D5157, 0x2000, 256, i, count, 0xADA52840),
                f"Unexpected UF2 header/family/block sequence at block {i}")
        require(address == 0x27000 + i * 256 and address + size <= 0xEC000,
                f"Unexpected UF2 application address at block {i}")
        require(struct.unpack("<I", block[-4:])[0] == 0x0AB16F30,
                f"Invalid UF2 trailing magic at block {i}")
        payload.extend(block[32:32 + size])
    return bytes(payload), count
