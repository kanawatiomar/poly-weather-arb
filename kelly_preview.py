import sys
sys.path.insert(0, r'C:\Users\kanaw\.openclaw\workspace\ventures\ventures\poly-weather-arb')
from auto_trade import kelly_size

examples = [
    ('Buenos Aires YES', 0.448, 0.012),
    ('Wellington NO',    0.373, 0.500),
    ('Atlanta YES',      0.425, 0.057),
    ('NYC YES',          0.382, 0.080),
    ('Dallas YES',       0.319, 0.170),
    ('Weak edge 21%',    0.210, 0.400),
]
print('Signal                      Edge    Price   Kelly Bet   Shares')
print('-' * 63)
for label, edge, price in examples:
    bet = kelly_size(edge, price)
    shares = round(bet / price, 1)
    print(f'{label:<27}  {edge:+.1%}   {price:.3f}    ${bet:5.2f}      {shares:.1f}')
