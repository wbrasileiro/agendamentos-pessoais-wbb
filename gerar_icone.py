import PIL.Image
import PIL.ImageDraw

# Criar tela transparente 256x256
size = (256, 256)
image = PIL.Image.new("RGBA", size, (0, 0, 0, 0))
draw = PIL.ImageDraw.Draw(image)

# Cores
bg_color = (37, 99, 235, 255)       # Azul
card_bg = (255, 255, 255, 255)      # Branco
header_color = (30, 64, 175, 255)   # Azul Escuro
accent_red = (239, 68, 68, 255)     # Vermelho
ring_color = (226, 232, 240, 255)   # Cinza Claro

# Fundo arredondado
pad, radius = 16, 48
draw.rounded_rectangle([pad, pad, size[0] - pad, size[1] - pad], radius=radius, fill=bg_color)

# Corpo do Calendário
c_left, c_top, c_right, c_bottom = 48, 64, 208, 208
c_radius = 20
draw.rounded_rectangle([c_left, c_top, c_right, c_bottom], radius=c_radius, fill=card_bg)

# Cabeçalho do Calendário
header_height = 44
draw.rounded_rectangle([c_left, c_top, c_right, c_top + header_height], radius=c_radius, fill=header_color)
draw.rectangle([c_left, c_top + c_radius, c_right, c_top + header_height], fill=header_color)

# Anéis do fichário (topo)
draw.rounded_rectangle([72, 48, 80, 76], radius=4, fill=ring_color)
draw.rounded_rectangle([176, 48, 184, 76], radius=4, fill=ring_color)

# Grade de dias
grid_top, grid_left = 122, 68
step_x, step_y = 36, 24

for row in range(3):
    for col in range(4):
        x = grid_left + col * step_x
        y = grid_top + row * step_y
        if row == 1 and col == 2:
            draw.rounded_rectangle([x - 2, y - 2, x + 14, y + 14], radius=4, fill=accent_red)
        else:
            draw.rounded_rectangle([x, y, x + 12, y + 12], radius=3, fill=(226, 232, 240, 255))

# Selo de confirmação (Check verde)
badge_cx, badge_cy, badge_r = 180, 180, 30
draw.ellipse([badge_cx - badge_r - 4, badge_cy - badge_r - 4, badge_cx + badge_r + 4, badge_cy + badge_r + 4], fill=bg_color)
draw.ellipse([badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r], fill=(16, 185, 129, 255))

# Desenhar ícone de check
check_points = [(badge_cx - 12, badge_cy), (badge_cx - 3, badge_cy + 10), (badge_cx + 12, badge_cy - 10)]
draw.line(check_points, fill=(255, 255, 255, 255), width=7, joint="round")

# Salvar arquivo .ico
image.save(
    "agendamento.ico", 
    format="ICO", 
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
)

print("Arquivo 'agendamento.ico' criado com sucesso na pasta atual!")