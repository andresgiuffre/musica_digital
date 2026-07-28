# trainer/orquestador_gemini_legacy.py
"""
Implementación anterior de orquestador_analizar usando Gemini (google-genai).

No está conectada a ninguna URL — se conserva solo para comparar resultados
lado a lado contra la versión con Claude en views.py mientras se valida la
migración. google-genai ya no está en requirements.txt, así que este módulo
solo funciona si lo instalás manualmente (pip install google-genai) y no se
importa desde ningún otro lugar del proyecto.
"""
import json
import os
from pydantic import BaseModel, Field
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
import music21


class AlertaBalance(BaseModel):
    compas: str = Field(description="Número de compás o rango")
    problema: str = Field(description="Descripción técnica del problema (ej. 'Los metales graves en f van a tapar por completo la línea melódica de las maderas medias').")
    sugerencia: str = Field(description="Solución orquestal concreta (ej. 'Bajar la dinámica de trombones a mp, o duplicar la melodía con violas y cornos al unísono para darle más densidad y cuerpo frente al metal').")

class SugerenciaColorDoblaje(BaseModel):
    seccion: str = Field(description="Nombre del grupo de instrumentos o pasaje analizado")
    critica: str = Field(description="Análisis del color actual (ej. 'La melodía principal en flauta sola en el registro medio suena delgada para el carácter épico que busca el acompañamiento de cuerdas en staccato').")
    alternativas: str = Field(description="Proponer 2 combinaciones avanzadas de doblaje detallando el efecto psicológico de cada una (ej. 'Opción A: Doblar con Oboe al unísono para un color más penetrante y rústico. Opción B: Doblar con Violines I a la octava superior para darle brillo cinematográfico').")

class OrchestrationAnalysis(BaseModel):
    resumen_estilo: str = Field(description="Breve análisis de la textura general detectada (ej. homofónica, contrapuntística, masiva) y la distribución del mapa orquestal.")
    alertas_balance: list[AlertaBalance]
    sugerencias_color_y_doblaje: list[SugerenciaColorDoblaje]
    ejercicio_practico: str = Field(description="Un ejercicio o restricción compositiva personalizada basada en los errores del usuario para que aplique en su próximo compás (ej. 'Escribí los siguientes 8 compases usando únicamente combinaciones de maderas y cuerdas bajas, sin usar metales ni percusión, para entrenar el balance de texturas blandas').")


@login_required
def orquestador_analizar_gemini(request):
    if request.method == 'POST' and request.FILES.get('score_file'):
        score_file = request.FILES['score_file']
        name = request.POST.get('name', score_file.name)

        from .models import ScoreAnalysis
        analysis = ScoreAnalysis.objects.create(
            user=request.user,
            name=name,
            score_file=score_file
        )

        try:
            file_path = analysis.score_file.path
            score = music21.converter.parse(file_path)
            parts = score.parts
            instrument_names = [p.partName for p in parts if p.partName]

            try:
                key_sig = score.analyze('key')
                key_str = str(key_sig)
            except:
                key_str = "Unknown"

            time_sig = score.recurse().getElementsByClass(music21.meter.TimeSignature)
            ts = time_sig[0].ratioString if time_sig else "Unknown"

            tempos = score.recurse().getElementsByClass(music21.tempo.MetronomeMark)
            tempo = tempos[0].number if tempos else "Unknown"

            measures_data = {}
            for part in parts:
                part_name = part.partName or "Instrumento Desconocido"
                measures = part.getElementsByClass(music21.stream.Measure)

                part_data = []
                for m in list(measures)[:8]:
                    m_dict = {'number': m.number, 'notes': []}
                    for element in m.recurse().notes:
                        if isinstance(element, music21.note.Note):
                            m_dict['notes'].append(f"{element.nameWithOctave} ({element.duration.type})")
                        elif isinstance(element, music21.chord.Chord):
                            chord_notes = "-".join([n.nameWithOctave for n in element.notes])
                            m_dict['notes'].append(f"[{chord_notes}] ({element.duration.type})")

                    dynamics = m.recurse().getElementsByClass(music21.dynamics.Dynamic)
                    for d in dynamics:
                        m_dict['notes'].append(f"Dinámica: {d.value}")

                    part_data.append(m_dict)
                measures_data[part_name] = part_data

            analysis_data = {
                'instruments': instrument_names,
                'key_signature': key_str,
                'time_signature': ts,
                'tempo': tempo,
                'measures_data': measures_data
            }

            if genai:
                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

                system_instruction = (
                    "Sos un Maestro de Orquestación Clásica y Cinematográfica de élite, con décadas de experiencia "
                    "analizando partituras de compositores como John Williams, Ravel, Stravinsky, Jeremy Soule y Nobuo Uematsu. "
                    "Tu objetivo no es enseñar teoría básica (rango de instrumentos o qué es un ostinato), sino auditar el "
                    "CRITERIO de orquestación, el BALANCE de frecuencias y el COLOR de los doblajes.\n\n"
                    "Cuando recibas la estructura de datos de una pieza (instrumentos, notas por compás y dinámicas), "
                    "debés devolver un análisis crítico estructurado estrictamente en formato JSON."
                )

                prompt = f"Por favor, analiza la siguiente estructura de datos musicales:\n{json.dumps(analysis_data, ensure_ascii=False)}"

                response = client.models.generate_content(
                    model='models/gemini-2.0-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=OrchestrationAnalysis,
                        temperature=0.7,
                    ),
                )

                final_data = json.loads(response.text)
            else:
                final_data = {
                    "error": "El SDK de google-genai no está instalado.",
                    "raw_music_data": analysis_data
                }

            analysis.analysis_data = final_data
            analysis.save()

            return JsonResponse({'status': 'success', 'data': final_data})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return render(request, 'trainer/orquestador_analizar.html')
