"""Provision Superset: database connections, datasets, charts and the «Рынок грузовых ставок» dashboard.

Runs inside the Superset container through the application ORM (the same way Superset loads its
examples), so it does not depend on REST authentication. Idempotent: objects are looked up by name
and recreated, so restarting the container converges to the same state.
"""
from __future__ import annotations

import json
import os
import uuid

from superset.app import create_app

app = create_app()

# Значения по умолчанию для нативных фильтров дашборда (не зашиты в сами графики).
DEFAULT_ROUTE = "Москва → Санкт-Петербург"
DEFAULT_BODY = "Тент"
DEFAULT_WEIGHT = "FTL"
DEFAULT_RELIABILITY = ["HIGH", "MEDIUM"]
DASHBOARD_SLUG = "freight-rates"


def metric(sql: str, label: str) -> dict:
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label, "optionName": f"metric_{uuid.uuid4().hex[:8]}"}


def where(column: str, operator: str, value) -> dict:
    return {"clause": "WHERE", "expressionType": "SIMPLE", "subject": column, "operator": operator,
            "comparator": value, "filterOptionName": f"filter_{uuid.uuid4().hex[:8]}"}


def where_sql(expression: str) -> dict:
    return {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": expression,
            "filterOptionName": f"filter_{uuid.uuid4().hex[:8]}"}


# name -> (dataset key, viz_type, form_data without datasource)
CHARTS: dict[str, tuple[str, str, dict]] = {
    "MAPE модели на тесте, %": ("model_quality", "big_number_total", {
        "metric": metric("MAX(mape_pct)", "MAPE, %"),
        "adhoc_filters": [where("is_champion", "==", True), where("split", "==", "test")],
        "y_axis_format": ".2f", "subheader": "чемпион, отложенные 3 недели",
    }),
    "R² модели на тесте": ("model_quality", "big_number_total", {
        "metric": metric("MAX(r2)", "R²"),
        "adhoc_filters": [where("is_champion", "==", True), where("split", "==", "test")],
        "y_axis_format": ".3f", "subheader": "доля объяснённой дисперсии",
    }),
    "Сделок в карантине": ("dq_summary", "big_number_total", {
        "metric": metric("SUM(rows_quarantined)", "Строк"),
        "adhoc_filters": [], "y_axis_format": ",d", "subheader": "записи с кодом причины",
    }),
    # Маршрут, кузов и вес не зашиты в график: их задают нативные фильтры дашборда.
    "Коридор ставок по выбранному маршруту": ("route_card", "echarts_timeseries_line", {
        "x_axis": "month", "time_grain_sqla": "P1M",
        "metrics": [metric("MAX(p25_trip)", "P25, ₽/рейс"), metric("MAX(median_trip)", "Медиана, ₽/рейс"),
                    metric("MAX(p75_trip)", "P75, ₽/рейс")],
        "groupby": [], "adhoc_filters": [], "row_limit": 1000,
        "show_legend": True, "rich_tooltip": True, "y_axis_format": ",.0f", "markerEnabled": True,
    }),
    "Медиана ₽/км по группам кузова на выбранном маршруте": ("route_card", "echarts_timeseries_line", {
        "x_axis": "month", "time_grain_sqla": "P1M",
        "metrics": [metric("MAX(median_km)", "Медиана, ₽/км")],
        "groupby": ["body_group"],
        "adhoc_filters": [],
        "row_limit": 1000, "show_legend": True, "rich_tooltip": True, "y_axis_format": ",.1f",
    }),
    # Уровень надёжности выбирается нативным фильтром (по умолчанию HIGH и MEDIUM).
    "Сеть маршрутов за последний месяц": ("route_network", "table", {
        "query_mode": "raw",
        "all_columns": ["route", "body_group", "weight_category", "p25_trip", "median_trip", "p75_trip",
                        "median_km", "trips_count", "carriers_count", "cv_rate", "mom_pct", "yoy_pct",
                        "reliability_level"],
        "order_by_cols": [json.dumps(["trips_count", False])],
        "adhoc_filters": [],
        "row_limit": 500, "table_timestamp_format": "smart_date", "show_cell_bars": True,
    }),
    "Факт против прогноза (чемпион), средняя ставка по дням": ("predictions", "echarts_timeseries_line", {
        "x_axis": "loading_date", "time_grain_sqla": "P1D",
        "metrics": [metric("AVG(actual_price_rub)", "Факт, ₽"), metric("AVG(predicted_price_rub)", "Прогноз, ₽")],
        "groupby": [], "adhoc_filters": [where("is_champion", "==", True)],
        "row_limit": 1000, "show_legend": True, "rich_tooltip": True, "y_axis_format": ",.0f",
    }),
    "Важность признаков CatBoost": ("feature_importance", "echarts_timeseries_bar", {
        "x_axis": "feature", "metrics": [metric("MAX(importance)", "Важность")], "groupby": [],
        "adhoc_filters": [where("model_name", "==", "catboost")],
        "orientation": "horizontal", "row_limit": 50, "x_axis_sort": "Важность", "x_axis_sort_asc": True,
    }),
    "Причины отбраковки": ("dq_summary", "pie", {
        "groupby": ["reason_code"], "metric": metric("SUM(rows_quarantined)", "Строк"),
        "adhoc_filters": [], "show_legend": True, "label_type": "key_value", "row_limit": 100,
    }),
    "ClickHouse: самые плотные направления последнего месяца": ("ch_route_card", "table", {
        "query_mode": "aggregate", "groupby": ["route"],
        "metrics": [metric("SUM(trips_count)", "Рейсов"), metric("SUM(carriers_count)", "Перевозчиков (по ячейкам)"),
                    metric("avg(median_km)", "Средняя медиана, ₽/км")],
        "adhoc_filters": [where_sql("month = toStartOfMonth(as_of)")],
        "row_limit": 15, "order_desc": True, "timeseries_limit_metric": metric("SUM(trips_count)", "Рейсов"),
    }),
}

LAYOUT = [  # rows of (chart name, width out of 12, height)
    [("MAPE модели на тесте, %", 4, 25), ("R² модели на тесте", 4, 25), ("Сделок в карантине", 4, 25)],
    [("Коридор ставок по выбранному маршруту", 6, 60), ("Медиана ₽/км по группам кузова на выбранном маршруте", 6, 60)],
    [("Сеть маршрутов за последний месяц", 12, 80)],
    [("Факт против прогноза (чемпион), средняя ставка по дням", 8, 60), ("Важность признаков CatBoost", 4, 60)],
    [("Причины отбраковки", 4, 55), ("ClickHouse: самые плотные направления последнего месяца", 8, 55)],
]

# Нативные фильтры: имя -> (столбец, график-источник значений, графики в зоне действия,
# значения по умолчанию, обязателен ли выбор). Зона действия задаётся именами графиков.
DETAIL_CHARTS = ["Коридор ставок по выбранному маршруту", "Медиана ₽/км по группам кузова на выбранном маршруте"]
NETWORK_CHART = "Сеть маршрутов за последний месяц"
FILTERS = [
    # Маршрут управляет только карточкой: сводная таблица должна показывать всю сеть.
    ("Маршрут", "route", "route_card", DETAIL_CHARTS, [DEFAULT_ROUTE], True),
    # Кузов не применяется к графику-сравнению групп кузова.
    ("Группа кузова", "body_group", "route_card",
     ["Коридор ставок по выбранному маршруту", NETWORK_CHART], [DEFAULT_BODY], False),
    ("Весовая категория", "weight_category", "route_card", DETAIL_CHARTS + [NETWORK_CHART], [DEFAULT_WEIGHT], False),
    # Месяцы — диапазон дат; столбец не нужен: применяется к оси времени графиков карточки.
    ("Месяцы", None, "route_card", DETAIL_CHARTS, None, False),
    ("Уровень надёжности", "reliability_level", "route_network", [NETWORK_CHART], DEFAULT_RELIABILITY, False),
]


def native_filter(name: str, column: str | None, dataset_id: int, charts_in_scope: list[int],
                  excluded: list[int], default: list | None, required: bool) -> dict:
    conf = {
        "id": f"NATIVE_FILTER-{column or 'time-range'}",
        "name": name,
        "filterType": "filter_select" if column else "filter_time",
        "type": "NATIVE_FILTER",
        "description": "",
        "targets": [{"column": {"name": column}, "datasetId": dataset_id}] if column else [{}],
        "controlValues": {"enableEmptyFilter": required, "defaultToFirstItem": False,
                          "multiSelect": True, "searchAllOptions": False, "inverseSelection": False}
        if column else {},
        "cascadeParentIds": [],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
        "chartsInScope": charts_in_scope,
        "tabsInScope": [],
        "defaultDataMask": {"extraFormData": {}, "filterState": {}},
    }
    if default:
        conf["defaultDataMask"] = {
            "extraFormData": {"filters": [{"col": column, "op": "IN", "val": default}]},
            "filterState": {"value": default, "label": ", ".join(map(str, default))},
        }
    return conf


def main() -> None:
    with app.app_context():
        from superset import db, security_manager
        from superset.connectors.sqla.models import SqlaTable
        from superset.models.core import Database
        from superset.models.dashboard import Dashboard
        from superset.models.slice import Slice

        owner = security_manager.find_user(username=os.environ.get("SUPERSET_ADMIN_USER", "admin"))

        def get_database(name: str, uri: str) -> Database:
            database = db.session.query(Database).filter_by(database_name=name).first() or Database(database_name=name)
            database.set_sqlalchemy_uri(uri)
            database.expose_in_sqllab = True
            database.allow_run_async = False
            db.session.add(database)
            db.session.commit()
            return database

        def get_dataset(database: Database, schema: str, table: str, dttm: str | None) -> SqlaTable | None:
            dataset = db.session.query(SqlaTable).filter_by(table_name=table, schema=schema, database_id=database.id).first()
            if dataset is None:
                dataset = SqlaTable(table_name=table, schema=schema, database=database)
                db.session.add(dataset)
            dataset.main_dttm_col = dttm
            dataset.owners = [owner] if owner else []
            try:
                dataset.fetch_metadata()
                db.session.commit()
                return dataset
            except Exception as exc:  # noqa: BLE001 - provisioning must not block startup
                db.session.rollback()
                print(f"WARNING: dataset {schema}.{table} skipped: {exc}")
                return None

        pg = get_database("Хранилище PostgreSQL (dwh)", os.environ["SUPERSET_DWH_URI"])
        datasets = {
            "route_card": get_dataset(pg, "mart", "mart_route_card", "month"),
            "route_network": get_dataset(pg, "mart", "v_route_network", "month"),
            "predictions": get_dataset(pg, "mart", "ml_price_predictions", "loading_date"),
            "model_quality": get_dataset(pg, "mart", "v_model_quality", None),
            "feature_importance": get_dataset(pg, "mart", "v_feature_importance", None),
            "dq_summary": get_dataset(pg, "mart", "v_dq_quarantine_summary", "loading_month"),
        }
        if os.environ.get("SUPERSET_CLICKHOUSE_URI"):
            ch = get_database("Аналитика ClickHouse", os.environ["SUPERSET_CLICKHOUSE_URI"])
            datasets["ch_route_card"] = get_dataset(ch, os.environ.get("CLICKHOUSE_DB", "freight"), "route_card", "month")

        slices: dict[str, Slice] = {}
        for name, (key, viz_type, form) in CHARTS.items():
            dataset = datasets.get(key)
            if dataset is None:
                continue
            existing = db.session.query(Slice).filter_by(slice_name=name).first()
            if existing:
                db.session.delete(existing)
                db.session.flush()
            params = {**form, "viz_type": viz_type, "datasource": f"{dataset.id}__table"}
            slc = Slice(slice_name=name, viz_type=viz_type, datasource_type="table", datasource_id=dataset.id,
                        params=json.dumps(params, ensure_ascii=False), owners=[owner] if owner else [])
            db.session.add(slc)
            slices[name] = slc
        db.session.commit()

        position = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
            "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
            "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": "Рынок грузовых ставок"}},
        }
        for r, row in enumerate(LAYOUT):
            row_id = f"ROW-{r}"
            position[row_id] = {"type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                                "meta": {"background": "BACKGROUND_TRANSPARENT"}}
            position["GRID_ID"]["children"].append(row_id)
            for c, (name, width, height) in enumerate(row):
                if name not in slices:
                    continue
                chart_id = f"CHART-{r}-{c}"
                slc = slices[name]
                position[chart_id] = {
                    "type": "CHART", "id": chart_id, "children": [], "parents": ["ROOT_ID", "GRID_ID", row_id],
                    "meta": {"chartId": slc.id, "sliceName": name, "uuid": str(slc.uuid), "width": width, "height": height},
                }
                position[row_id]["children"].append(chart_id)

        all_chart_ids = {s.id for s in slices.values()}
        filter_configuration = []
        for name, column, dataset_key, scope_names, default, required in FILTERS:
            if datasets.get(dataset_key) is None:
                continue
            in_scope = [slices[c].id for c in scope_names if c in slices]
            filter_configuration.append(native_filter(
                name, column, datasets[dataset_key].id, in_scope,
                sorted(all_chart_ids - set(in_scope)), default, required))

        dash = db.session.query(Dashboard).filter_by(slug=DASHBOARD_SLUG).first() or Dashboard(slug=DASHBOARD_SLUG)
        dash.dashboard_title = "Рынок грузовых ставок"
        dash.position_json = json.dumps(position, ensure_ascii=False)
        dash.json_metadata = json.dumps({"color_scheme": "supersetColors", "refresh_frequency": 0,
                                         "native_filter_configuration": filter_configuration},
                                        ensure_ascii=False)
        dash.slices = list(slices.values())
        dash.owners = [owner] if owner else []
        dash.published = True
        db.session.add(dash)
        db.session.commit()
        print(f"Provisioned dashboard /superset/dashboard/{DASHBOARD_SLUG}/ with {len(slices)} charts")


if __name__ == "__main__":
    main()
