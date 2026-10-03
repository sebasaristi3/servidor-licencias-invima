"""
Servidor de licencias, receta y uso - Autofill formularios
================================================================
Tres responsabilidades:
  1. Validar si un cliente tiene licencia activa (por su API key), contra
     una base de datos Postgres real (ya no un diccionario en memoria).
  2. Si la tiene, entregarle la "receta" (los datos que el programa local
     necesita para funcionar, ej. los IDs de los campos del formulario).
  3. Registrar cuantos ingredientes se procesaron en cada corrida, para
     estadisticas de uso por cliente.

La API key va en el header X-API-Key en cada llamada del cliente. Sin una
API key valida y activa, no se entrega nada -- asi el programa local nunca
es util por si solo, aunque alguien copie el .exe/.py y se lo pase a otra
persona.

Panel de administracion simple en /admin (protegido con una contrasena,
variable de entorno ADMIN_PASSWORD): agregar/activar/desactivar clientes y
ver cuanto ha corrido cada uno.

Requiere la variable de entorno DATABASE_URL (Railway la agrega sola al
conectar un plugin de Postgres al proyecto) y, opcionalmente,
ADMIN_PASSWORD (si no se define, usa un valor por defecto que DEBE
cambiarse antes de usar el panel en serio).
"""

import os
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Autofill formularios - Servidor de licencias")

DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CAMBIAR-ESTA-CLAVE-DE-ADMIN")

RECETA_AGREGAR_INGREDIENTE = {
    "boton_anadir": "text=Añadir Ingrediente",
    "tipo": "itemType",
    "ingrediente": "ingredientId",
    "funcion": "functionSlugs",
    "listado_referencia": "referenceListSlug",
    "cantidad": "quantity",
    "unidad_medida": "unitSlug",
    "switch_nanomaterial": "isNanomaterial",
    "boton_guardar": "button:has-text('Guardar')",
}

with open(os.path.join(os.path.dirname(__file__), "admin.html"), encoding="utf-8") as f:
    _ADMIN_HTML = f.read()


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS clientes (
                    api_key TEXT PRIMARY KEY,
                    empresa TEXT NOT NULL,
                    nit TEXT,
                    activo BOOLEAN NOT NULL DEFAULT TRUE,
                    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS usos (
                    id SERIAL PRIMARY KEY,
                    api_key TEXT NOT NULL REFERENCES clientes(api_key),
                    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
                    total_filas INTEGER NOT NULL,
                    exitosas INTEGER NOT NULL
                )
                """
            )
            # ALTER ... ADD COLUMN IF NOT EXISTS es idempotente: sirve tanto para
            # una base de datos nueva (ya la crea CREATE TABLE de arriba, esto no
            # hace nada) como para una ya existente en producción (le agrega las
            # columnas nuevas sin perder los datos que ya tiene).
            cur.execute("ALTER TABLE usos ADD COLUMN IF NOT EXISTS duracion_segundos REAL")
            cur.execute("ALTER TABLE usos ADD COLUMN IF NOT EXISTS ingrediente_fallido TEXT")
            cur.execute("ALTER TABLE usos ADD COLUMN IF NOT EXISTS version_exe TEXT")
        conn.commit()


init_db()


def validar_cliente(api_key: str) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM clientes WHERE api_key = %s", (api_key,))
            cliente = cur.fetchone()
    if not cliente or not cliente["activo"]:
        raise HTTPException(status_code=403, detail="Licencia no encontrada o inactiva")
    return cliente


def verificar_admin(x_admin_password: str = Header(..., alias="X-Admin-Password")):
    if x_admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Contraseña de administrador incorrecta")


@app.get("/")
def estado():
    """Solo para confirmar que el servidor esta vivo (lo puedes abrir en el navegador)."""
    return {"status": "ok", "servicio": "Autofill formularios - Servidor de licencias"}


@app.post("/receta/agregar-ingrediente")
def obtener_receta_ingredientes(x_api_key: str = Header(..., alias="X-API-Key")):
    cliente = validar_cliente(x_api_key)
    return {"empresa": cliente["empresa"], "campos": RECETA_AGREGAR_INGREDIENTE}


class RegistroUso(BaseModel):
    total_filas: int
    exitosas: int
    # Los tres siguientes son opcionales a propósito: así un .exe viejo que
    # todavía no los envía sigue funcionando sin romper nada (quedan como
    # NULL en esa fila), y no hace falta forzar a todos los clientes a
    # actualizar el mismo día que se agrega un dato nuevo.
    duracion_segundos: Optional[float] = None
    ingrediente_fallido: Optional[str] = None
    version_exe: Optional[str] = None


@app.post("/uso/registrar")
def registrar_uso(datos: RegistroUso, x_api_key: str = Header(..., alias="X-API-Key")):
    validar_cliente(x_api_key)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usos (api_key, total_filas, exitosas, duracion_segundos, ingrediente_fallido, version_exe)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (x_api_key, datos.total_filas, datos.exitosas, datos.duracion_segundos, datos.ingrediente_fallido, datos.version_exe),
            )
        conn.commit()
    return {"status": "ok"}


# ------------------------------------------------------------------
# Panel de administracion (solo Sebastian, protegido con ADMIN_PASSWORD)
# ------------------------------------------------------------------


class ClienteIn(BaseModel):
    api_key: str
    empresa: str
    nit: Optional[str] = None
    activo: bool = True


@app.get("/admin/clientes", dependencies=[Depends(verificar_admin)])
def listar_clientes():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.api_key, c.empresa, c.nit, c.activo, c.creado_en,
                       COUNT(u.id) AS corridas,
                       COALESCE(SUM(u.total_filas), 0) AS total_filas,
                       COALESCE(SUM(u.exitosas), 0) AS total_exitosas
                FROM clientes c
                LEFT JOIN usos u ON u.api_key = c.api_key
                GROUP BY c.api_key
                ORDER BY c.creado_en DESC
                """
            )
            filas = cur.fetchall()
    return filas


@app.get("/admin/uso-diario", dependencies=[Depends(verificar_admin)])
def uso_diario():
    """Uso por día, desglosado por cliente (una fila por cliente y día), últimos 60 días."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT date_trunc('day', u.fecha) AS dia,
                       c.empresa,
                       u.api_key,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(u.total_filas), 0) AS total_filas,
                       COALESCE(SUM(u.exitosas), 0) AS total_exitosas
                FROM usos u
                JOIN clientes c ON c.api_key = u.api_key
                WHERE u.fecha >= now() - interval '60 days'
                GROUP BY dia, c.empresa, u.api_key
                ORDER BY dia DESC, c.empresa
                """
            )
            filas = cur.fetchall()
    return filas


@app.get("/admin/actividad-clientes", dependencies=[Depends(verificar_admin)])
def actividad_clientes():
    """
    Por cliente: cuándo fue su última corrida (y hace cuántos días, para
    detectar clientes inactivos/en riesgo de cancelar), cuántos ingredientes
    sube en promedio por corrida, y cada cuántos días corre el programa en
    promedio (frecuencia real de uso).
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.empresa,
                       c.api_key,
                       MAX(u.fecha) AS ultima_corrida,
                       EXTRACT(DAY FROM now() - MAX(u.fecha))::int AS dias_inactivo,
                       COUNT(u.id) AS corridas,
                       ROUND(AVG(u.total_filas)::numeric, 1)::float AS promedio_filas_por_corrida,
                       CASE WHEN COUNT(u.id) > 1
                            THEN ROUND((EXTRACT(EPOCH FROM (MAX(u.fecha) - MIN(u.fecha))) / 86400.0 / (COUNT(u.id) - 1))::numeric, 1)::float
                            ELSE NULL
                       END AS frecuencia_dias
                FROM clientes c
                LEFT JOIN usos u ON u.api_key = c.api_key
                GROUP BY c.empresa, c.api_key
                ORDER BY ultima_corrida DESC NULLS LAST
                """
            )
            filas = cur.fetchall()
    return filas


@app.get("/admin/corridas-recientes", dependencies=[Depends(verificar_admin)])
def corridas_recientes():
    """Log crudo (sin agregar) de las últimas 50 corridas, con detalle de duración y qué falló."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.fecha, c.empresa, u.total_filas, u.exitosas,
                       u.duracion_segundos, u.ingrediente_fallido, u.version_exe
                FROM usos u
                JOIN clientes c ON c.api_key = u.api_key
                ORDER BY u.fecha DESC
                LIMIT 50
                """
            )
            filas = cur.fetchall()
    return filas


@app.get("/admin/uso-por-horario", dependencies=[Depends(verificar_admin)])
def uso_por_horario():
    """
    Todas las corridas juntas, agrupadas por hora del día y por día de la
    semana (hora local de Colombia) -- util para saber cuándo priorizar
    soporte o anunciar mantenimientos del servidor.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT EXTRACT(HOUR FROM fecha AT TIME ZONE 'America/Bogota')::int AS hora,
                       COUNT(*) AS corridas
                FROM usos
                GROUP BY hora
                ORDER BY hora
                """
            )
            por_hora = cur.fetchall()
            cur.execute(
                """
                SELECT EXTRACT(DOW FROM fecha AT TIME ZONE 'America/Bogota')::int AS dia_semana,
                       COUNT(*) AS corridas
                FROM usos
                GROUP BY dia_semana
                ORDER BY dia_semana
                """
            )
            por_dia_semana = cur.fetchall()
    return {"por_hora": por_hora, "por_dia_semana": por_dia_semana}


@app.post("/admin/clientes", dependencies=[Depends(verificar_admin)])
def crear_o_actualizar_cliente(cliente: ClienteIn):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO clientes (api_key, empresa, nit, activo)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (api_key) DO UPDATE
                SET empresa = EXCLUDED.empresa, nit = EXCLUDED.nit, activo = EXCLUDED.activo
                """,
                (cliente.api_key, cliente.empresa, cliente.nit, cliente.activo),
            )
        conn.commit()
    return {"status": "ok"}


@app.patch("/admin/clientes/{api_key}", dependencies=[Depends(verificar_admin)])
def cambiar_estado_cliente(api_key: str, activo: bool):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE clientes SET activo = %s WHERE api_key = %s", (activo, api_key))
        conn.commit()
    return {"status": "ok"}


@app.get("/admin", response_class=HTMLResponse)
def panel_admin():
    return _ADMIN_HTML
