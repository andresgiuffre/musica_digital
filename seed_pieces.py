import os
import django
import random

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from trainer.models import Piece, Game

game, created = Game.objects.get_or_create(slug='lectura-musical')
game.name = 'Lectura Musical'
game.description = 'Aprende a leer partituras progresivas generadas proceduralmente con sentido melódico.'
game.order = 5
game.recommended_accuracy = 0
game.recommended_attempts = 10
game.save()

Piece.objects.all().delete()

# C Major Scale over 2 octaves
FULL_SCALE = [
    ('G', 3), ('A', 3), ('B', 3), 
    ('C', 4), ('D', 4), ('E', 4), ('F', 4), ('G', 4), ('A', 4), ('B', 4), 
    ('C', 5), ('D', 5), ('E', 5), ('F', 5), ('G', 5)
]

def generate_musicxml(num_measures, time_num, time_den, allowed_dur_names, allowed_steps):
    divisions = 2
    beats_per_measure_quarter = time_num * (4 / time_den)
    measure_divs = int(beats_per_measure_quarter * divisions)

    dur_map = {
        'whole': 8,
        'half': 4,
        'quarter': 2,
        'eighth': 1
    }
    allowed_divs = [dur_map[d] for d in allowed_dur_names if d in dur_map]

    # Encontrar índices válidos dentro de FULL_SCALE basados en allowed_steps
    valid_indices = [i for i, step in enumerate(FULL_SCALE) if step in allowed_steps]
    
    # Empezar en la tónica (C4) si es posible, o en el medio
    start_index = next((i for i, step in enumerate(FULL_SCALE) if step == ('C', 4) and i in valid_indices), valid_indices[len(valid_indices)//2])
    current_index = start_index

    xml = []
    xml.append('<?xml version="1.0" encoding="UTF-8"?>')
    xml.append('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')
    xml.append('<score-partwise version="3.1">')
    xml.append('  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>')
    xml.append('  <part id="P1">')

    total_notes_generated = 0

    for m in range(1, num_measures + 1):
        xml.append(f'    <measure number="{m}">')
        if m == 1:
            xml.append(f'      <attributes><divisions>{divisions}</divisions><key><fifths>0</fifths></key><time><beats>{time_num}</beats><beat-type>{time_den}</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>')
        
        rem = measure_divs
        
        # Último compás: forzar nota larga en la tónica
        if m == num_measures:
            if 'whole' in allowed_dur_names and measure_divs >= 8:
                rem = 8
                allowed_here = [8]
            elif 'half' in allowed_dur_names and measure_divs >= 4:
                rem = 4
                allowed_here = [4]
            else:
                allowed_here = [max(valid_divs for valid_divs in allowed_divs if valid_divs <= measure_divs)]
                
            while rem > 0:
                choice = allowed_here[0] if allowed_here[0] <= rem else rem
                rem -= choice
                
                step, octave = FULL_SCALE[start_index] # Terminar en la tónica
                type_str = next((k for k,v in dur_map.items() if v == choice), "quarter")
                xml.append(f'      <note><pitch><step>{step}</step><octave>{octave}</octave></pitch><duration>{choice}</duration><type>{type_str}</type></note>')
            
            xml.append('      <barline location="right"><bar-style>light-heavy</bar-style></barline>')
            xml.append('    </measure>')
            break

        while rem > 0:
            valid_divs = [d for d in allowed_divs if d <= rem]
            if not valid_divs:
                break
                
            choice = random.choice(valid_divs)
            rem -= choice
            
            # Algoritmo de paseo aleatorio musical (Random Walk)
            # 60% grado conjunto (+1 o -1)
            # 20% salto de tercera (+2 o -2)
            # 10% misma nota (0)
            # 10% salto grande (+3, -3, +4, -4) pero obligando a regresar
            deltas = [-1, 1, -2, 2, 0, -3, 3, -4, 4]
            weights = [30, 30, 10, 10, 10, 2, 2, 3, 3]
            
            # Si estamos en los extremos, forzamos a volver al centro
            if current_index <= min(valid_indices) + 1:
                weights = [0, 50, 0, 30, 5, 0, 15, 0, 0] # Solo subir
            elif current_index >= max(valid_indices) - 1:
                weights = [50, 0, 30, 0, 5, 15, 0, 0, 0] # Solo bajar
                
            delta = random.choices(deltas, weights=weights)[0]
            next_index = current_index + delta
            
            # Asegurar que esté dentro del rango permitido
            if next_index not in valid_indices:
                # Buscar el más cercano válido
                next_index = min(valid_indices, key=lambda x: abs(x - next_index))
            
            # Cadencia al final (Penúltimo compás empuja hacia Supertónica o Sensible)
            if m == num_measures - 1 and rem == 0:
                # La última nota antes del final
                next_index = start_index + random.choice([-1, 1]) 
            
            current_index = next_index
            step, octave = FULL_SCALE[current_index]
            
            type_str = next((k for k,v in dur_map.items() if v == choice), "quarter")
            xml.append(f'      <note><pitch><step>{step}</step><octave>{octave}</octave></pitch><duration>{choice}</duration><type>{type_str}</type></note>')
            total_notes_generated += 1

        xml.append('    </measure>')

    xml.append('  </part>')
    xml.append('</score-partwise>')
    
    return "\n".join(xml)

# Configuración de niveles
basic_steps = [('C',4), ('D',4), ('E',4), ('F',4), ('G',4)]
medium_steps = [('C',4), ('D',4), ('E',4), ('F',4), ('G',4), ('A',4), ('B',4), ('C',5)]
large_steps = [('G',3), ('A',3), ('B',3), ('C',4), ('D',4), ('E',4), ('F',4), ('G',4), ('A',4), ('B',4), ('C',5), ('D',5), ('E',5)]

levels = [
    {
        'title': "Estudio Nivel 1 (Melodía Básica)",
        'time_num': 4, 'time_den': 4,
        'num_measures': 4,
        'allowed_durs': ['whole', 'half', 'quarter'],
        'allowed_steps': basic_steps
    },
    {
        'title': "Estudio Nivel 2 (Vals Cantabile)",
        'time_num': 3, 'time_den': 4,
        'num_measures': 8,
        'allowed_durs': ['half', 'quarter'],
        'allowed_steps': medium_steps
    },
    {
        'title': "Estudio Nivel 3 (Andante Lineal)",
        'time_num': 4, 'time_den': 4,
        'num_measures': 20,
        'allowed_durs': ['half', 'quarter'],
        'allowed_steps': medium_steps
    },
    {
        'title': "Estudio Nivel 4 (Marcha Melódica)",
        'time_num': 2, 'time_den': 4,
        'num_measures': 30,
        'allowed_durs': ['half', 'quarter'],
        'allowed_steps': large_steps
    },
    {
        'title': "Estudio Nivel 5 (Corcheas Expresivas)",
        'time_num': 4, 'time_den': 4,
        'num_measures': 40,
        'allowed_durs': ['half', 'quarter', 'eighth'],
        'allowed_steps': large_steps
    }
]

for i, lvl in enumerate(levels):
    xml = generate_musicxml(
        num_measures=lvl['num_measures'],
        time_num=lvl['time_num'],
        time_den=lvl['time_den'],
        allowed_dur_names=lvl['allowed_durs'],
        allowed_steps=lvl['allowed_steps']
    )
    
    Piece.objects.create(
        title=lvl['title'],
        author='Melodic Engine',
        time_signature=f"{lvl['time_num']}/{lvl['time_den']}",
        key_signature='C',
        difficulty=i+1,
        xml_content=xml
    )

print("5 Procedural Melodic Pieces seeded successfully.")
