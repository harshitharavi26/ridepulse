"""Presentation-only chart and table transformations; source identifiers stay intact."""
import pandas as pd
import plotly.express as px

LABELS = {
    'station_name':'Station', 'station_id':'Station ID', 'trip_count':'Trips',
    'start_station_name':'From', 'end_station_name':'To',
    'start_station_id':'Origin ID', 'end_station_id':'Destination ID',
    'bikes_available':'Bikes', 'docks_available':'Docks', 'is_stale':'Stale',
    'collected_at':'Collected (UTC)', 'last_reported':'Upstream report (UTC)',
    'is_renting':'Renting', 'is_returning':'Returning', 'is_installed':'Installed',
    'observed_samples':'Observed readings', 'eligible_samples':'Known eligible readings',
    'low_samples':'Low readings', 'low_percent':'Low readings (%)',
    'first_observed_at':'First collection (UTC)', 'last_observed_at':'Last collection (UTC)',
    'latitude':'Latitude', 'longitude':'Longitude', 'capacity':'Capacity',
}
COMPACT = {
    'leaderboard':['station_name','trip_count'],
    'routes':['start_station_name','end_station_name','trip_count'],
    'availability':['station_name','bikes_available','docks_available','is_stale'],
    'low':['station_name','eligible_samples','low_samples','low_percent'],
    'directory':['station_name','station_id'],
}
DETAILS = {
    'leaderboard':['station_id'], 'routes':['start_station_id','end_station_id'],
    'availability':['station_id','collected_at','last_reported','is_installed','is_renting','is_returning'],
    'low':['station_id','observed_samples','first_observed_at','last_observed_at'],
    'directory':['latitude','longitude','capacity'],
}


def make_chart(rows, x, y, title, granularity=None):
    frame=pd.DataFrame(rows)
    if x=='station_id':
        names=frame['station_name'].fillna('Unnamed station').replace('', 'Unnamed station')
        ambiguous=names.duplicated(keep=False) | names.eq('Unnamed station')
        frame['station']=names.where(~ambiguous,names+' · '+frame['station_id'].astype(str))
        figure=px.bar(frame.iloc[::-1],x=y,y='station',orientation='h',title=title,
                      custom_data=['station_id'],labels={y:'Trips','station':'Station'},
                      color_discrete_sequence=['#0f766e'],height=620)
        figure.update_yaxes(type='category',title=None)
        figure.update_xaxes(title='Trips')
        figure.update_traces(hovertemplate='<b>%{y}</b><br>Trips: %{x:,}<br>Station ID: %{customdata[0]}<extra></extra>')
    else:
        axis={'hour':'Local hour','weekday':'Weekday','day':'Date'}[granularity]
        if granularity=='hour':
            frame['period']=frame[x].map(lambda value:f'{int(value):02d}:00')
        elif granularity=='weekday':
            weekdays=dict(enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'],1))
            frame['period']=frame[x].map(weekdays)
        else:
            frame['period']=frame[x]
        figure=px.bar(frame,x='period',y=y,title=title,labels={'period':axis,y:'Trips'},
                      color_discrete_sequence=['#0f766e'],height=400)
        if granularity!='day':
            figure.update_xaxes(type='category',categoryorder='array',categoryarray=frame['period'].tolist())
        figure.update_traces(hovertemplate='%{x}<br>Trips: %{y:,}<extra></extra>')
    figure.update_layout(template='plotly_white',margin=dict(l=10,r=10,t=55,b=45))
    return figure


def table_view(rows,kind,details=False):
    frame=pd.DataFrame(rows).copy()
    for name,identifier in [('station_name','station_id'),('start_station_name','start_station_id'),('end_station_name','end_station_id')]:
        if identifier in frame:
            if name not in frame:frame[name]=None
            frame[name]=frame[name].fillna(frame[identifier])
            frame[name]=frame[name].where(frame[name].ne(''),frame[identifier])
    if 'low_sample_fraction' in frame:
        frame['low_percent']=frame['low_sample_fraction']*100
    columns=COMPACT[kind]+(DETAILS[kind] if details else [])
    return frame[[column for column in columns if column in frame]].rename(columns=LABELS)


def availability_map(rows):
    """Observed fill fraction, never guessed capacity or freshness."""
    import plotly.graph_objects as go
    frame=pd.DataFrame(rows)
    frame=frame.dropna(subset=['latitude','longitude'])
    if frame.empty:
        return None
    def colour(row):
        bikes,docks=row.get('bikes_available'),row.get('docks_available')
        if row.get('is_stale') or pd.isna(bikes) or pd.isna(docks) or bikes+docks<=0:
            return '#64748b'
        ratio=max(0,min(1,bikes/(bikes+docks)))
        return f'rgb({round(220*(1-ratio)+22*ratio)},{round(38*(1-ratio)+163*ratio)},{round(38*(1-ratio)+74*ratio)})'
    frame['colour']=frame.apply(colour,axis=1)
    frame['name']=frame['station_name'].fillna(frame['station_id'])
    figure=go.Figure(go.Scattermapbox(lat=frame.latitude,lon=frame.longitude,mode='markers',
        marker=dict(size=12,color=frame.colour),text=frame['name'],
        customdata=frame[['bikes_available','docks_available','is_stale']].to_numpy(),
        hovertemplate='<b>%{text}</b><br>Bikes: %{customdata[0]} · Docks: %{customdata[1]}<br>Stale: %{customdata[2]}<extra></extra>'))
    figure.update_layout(mapbox=dict(style='open-street-map',center=dict(lat=float(frame.latitude.mean()),lon=float(frame.longitude.mean())),zoom=11),
                          height=420,margin=dict(l=0,r=0,t=0,b=0))
    return figure
