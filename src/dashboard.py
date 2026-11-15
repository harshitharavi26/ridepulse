"""Streamlit presentation layer; all data access goes through the REST API."""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from google.auth.exceptions import GoogleAuthError
from dashboard_views import make_chart,table_view

st.set_option("client.toolbarMode", "viewer")
st.set_page_config(page_title="RidePulse", page_icon="🚲", layout="wide")
API = os.getenv("API_BASE_URL", "http://localhost:8080").rstrip("/")


@st.cache_data(ttl=2700, show_spinner=False)
def service_token(audience):
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token
    return fetch_id_token(Request(), audience)


def fetch(path, **params):
    try:
        audience = os.getenv("API_AUTH_AUDIENCE")
        headers = {"Authorization": "Bearer " + service_token(audience)} if audience else {}
        result = requests.get(API + path, params=params, headers=headers, timeout=(3, 30))
        if result.status_code == 503:
            st.error("The data service is temporarily unavailable. The database may be paused; try again later.")
            return None
        result.raise_for_status()
        return result.json()
    except (requests.RequestException, ValueError, GoogleAuthError):
        st.error("Could not load this view. Check the API connection and try refreshing.")
        return None


def chart(rows,x,y,title,granularity=None):
    if not rows:
        st.info("No observations match these filters.")
        return
    st.plotly_chart(make_chart(rows,x,y,title,granularity),use_container_width=True)


def table(rows,kind):
    frame=table_view(rows,kind,show_details)
    config={name:st.column_config.TextColumn(name,width="medium") for name in ('Station','From','To') if name in frame}
    for name in ('Trips','Bikes','Docks','Low readings','Known eligible readings'):
        if name in frame:config[name]=st.column_config.NumberColumn(name,width="small",format="%d")
    if 'Low readings (%)' in frame:config['Low readings (%)']=st.column_config.NumberColumn('Low readings (%)',format='%.1f',width='small')
    st.dataframe(frame,column_config=config,use_container_width=True,hide_index=True)


def local_date(value, timezone):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo(timezone)).date()


st.title("RidePulse")
st.caption("Explore NYC bike-share trips and observed station availability in NYC and Boulder.")
city = st.sidebar.selectbox("City", ["nyc", "boulder"], format_func=lambda v: {"nyc": "New York City", "boulder": "Boulder"}[v])
view = st.sidebar.radio("Explore", ["Trip history", "Station availability"])
show_details=st.sidebar.checkbox("Show station IDs and details",value=False)
if st.sidebar.button("Refresh data"):
    st.rerun()

if view == "Trip history":
    if city == "boulder":
        st.info("Boulder is our station-availability case study. Historical trip data is available for NYC; select Station availability to explore Boulder.")
        st.stop()
    cities = fetch("/v1/cities")
    if cities is None:
        st.stop()
    coverage = next(c for c in cities["data"] if c["city"] == "nyc")["trip_coverage"]
    if not coverage or not coverage["first_event_at"]:
        st.info("No NYC trip history is loaded in this database. The cloud dataset is separate from the empty local development database.")
        st.stop()
    first = local_date(coverage["first_event_at"], "America/New_York")
    last = local_date(coverage["last_event_at"], "America/New_York")
    st.caption(f"Stored trip starts: {first:%b %d, %Y} – {last:%b %d, %Y}. Dates and commute hours use New York local time.")
    start = st.sidebar.date_input("From", value=first, key="trip_start")
    end = st.sidebar.date_input("Through (inclusive)", value=min(last, first + timedelta(days=30)), key="trip_end")
    if end < start or (end-start).days >= 366:
        st.warning("Choose an ordered range of at most 366 days.")
        st.stop()
    metric = st.sidebar.selectbox("Count", ["pickups", "dropoffs"])
    station = st.sidebar.text_input("Trip station ID (optional)", key="trip_station").strip()
    params = dict(city=city, start_date=start.isoformat(), end_date=(end+timedelta(days=1)).isoformat(), metric=metric)
    if station:
        params["station_id"] = station
    st.caption("Pickup dates use departure times; drop-off dates use arrival times. Station IDs from trip history can differ from live-feed IDs.")
    top = fetch("/v1/trips/leaderboard", **params, limit=20)
    if top:
        chart(top["data"],"station_id","trip_count",f"Top {metric}")
        with st.expander("Ranked station table"):
            table(top["data"],"leaderboard")
    granularity=st.selectbox("Pattern",["hour","weekday","day"])
    trend=fetch("/v1/trips/patterns",**params,granularity=granularity)
    if trend:
        st.metric("Trips in selection",f"{sum(row['trip_count'] for row in trend['data']):,}")
        chart(trend["data"],"bucket","trip_count",f"{metric.title()} by local {granularity}",granularity)
    st.subheader("Common routes")
    route_params = {k: v for k, v in params.items() if k not in ("metric", "station_id")}
    if station:
        route_params["start_station_id"] = station
    routes = fetch("/v1/trips/routes", **route_params, limit=20)
    if routes:
        table(routes["data"],"routes")
        st.caption("Routes use departure dates and exclude trips with an unknown endpoint. The station filter selects the route origin.")
    with st.expander("Find trip station IDs"):
        if st.button("Load station directory"):
            directory = fetch("/v1/stations", city=city, source="trips", limit=200)
            if directory:
                table(directory["data"],"directory")
                st.caption("First 200 IDs. The API supports pagination for the full directory.")
else:
    timezone = "America/New_York" if city == "nyc" else "America/Denver"
    st.subheader(f"{city.upper() if city == 'nyc' else 'Boulder'} station observations")
    st.caption("Station conditions are observations, not reconstructed trips. Archived snapshots may be newer than SQL while ingestion is paused.")
    station = st.sidebar.text_input("Availability station ID (optional)", key="live_station").strip()
    page = st.sidebar.number_input("Station page", min_value=1, max_value=51, value=1)
    params = dict(city=city, limit=200, offset=(page-1)*200)
    if station:
        params["station_id"] = station
    latest = fetch("/v1/availability/latest", **params)
    if latest:
        rows = latest["data"]
        if not rows:
            st.info("No stored station observations match this selection. Collection may be archiving data while SQL processing is paused.")
        else:
            stale = sum(row["is_stale"] for row in rows)
            a, b, c = st.columns(3)
            a.metric("Stations on this page", len(rows))
            b.metric("Fresh readings", len(rows)-stale)
            c.metric("Stale readings", stale)
            if stale:
                st.warning("Some readings are stale. Check collected_at and last_reported before treating counts as current.")
            df = pd.DataFrame(rows)
            table(rows,"availability")
            st.caption("Station names are shown by default. Enable Show station IDs and details for identifiers, UTC timestamps and operating flags.")
            coords = df.dropna(subset=["latitude", "longitude"])
            if not coords.empty:
                st.map(coords.rename(columns={"latitude": "lat", "longitude": "lon"})[["lat", "lon"]])
            st.caption("The map locates stored stations; it does not imply the readings are fresh. Pages contain up to 200 stations.")
    st.subheader("Observed low availability")
    today = datetime.now(ZoneInfo(timezone)).date()
    start = st.date_input("Observation date", value=today, key="low_date")
    metric = st.selectbox("Availability metric", ["bikes", "docks"])
    threshold = st.number_input("Low availability means count ≤", min_value=0, max_value=100, value=2)
    low_params = dict(city=city, start_date=start.isoformat(), end_date=(start+timedelta(days=1)).isoformat(), metric=metric, threshold=threshold, limit=200, offset=(page-1)*200)
    if station:
        low_params["station_id"] = station
    low = fetch("/v1/availability/low", **low_params)
    if low:
        if low["data"]:
            table(low["data"],"low")
        else:
            st.info("No observations are stored for this local date and station selection.")
        st.caption("Fractions count observed known readings at installed stations enabled for renting/returning. Missing samples are not zero bikes or continuous shortage periods.")
