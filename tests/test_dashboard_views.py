"""Regression checks for readable station labels and compact presentation."""
from pathlib import Path
import sys
import unittest
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dashboard_views import make_chart,table_view


class DashboardViewTests(unittest.TestCase):
    def test_numeric_looking_station_ids_stay_categorical(self):
        rows=[{'station_id':'6140.05','station_name':'West 21st Street','trip_count':14},
              {'station_id':'6233.04','station_name':'Chelsea Piers','trip_count':12}]
        chart=make_chart(rows,'station_id','trip_count','Top pickups')
        self.assertEqual(chart.data[0].orientation,'h')
        self.assertEqual(chart.layout.yaxis.type,'category')
        self.assertEqual(list(chart.data[0].y),['Chelsea Piers','West 21st Street'])
        self.assertEqual(chart.data[0].customdata[0][0],'6233.04')

    def test_duplicate_station_names_have_distinct_labels(self):
        rows=[{'station_id':'A','station_name':'Main Street','trip_count':2},
              {'station_id':'B','station_name':'Main Street','trip_count':1}]
        chart=make_chart(rows,'station_id','trip_count','Top pickups')
        self.assertEqual(len(set(chart.data[0].y)),2)

    def test_weekday_chart_uses_names_and_friendly_axes(self):
        chart=make_chart([{'bucket':1,'trip_count':2},{'bucket':7,'trip_count':3}],
                         'bucket','trip_count','Weekly pickups','weekday')
        self.assertEqual(list(chart.data[0].x),['Monday','Sunday'])
        self.assertEqual(chart.layout.xaxis.title.text,'Weekday')
        self.assertEqual(chart.layout.yaxis.title.text,'Trips')

    def test_compact_table_hides_uuid_but_details_preserve_it(self):
        rows=[{'station_id':'long-station-uuid','station_name':'Library','bikes_available':0,
               'docks_available':4,'is_stale':False,'collected_at':'2026-11-15T00:00Z'}]
        compact=table_view(rows,'availability')
        self.assertEqual(list(compact),['Station','Bikes','Docks','Stale'])
        detailed=table_view(rows,'availability',True)
        self.assertEqual(detailed.iloc[0]['Station ID'],'long-station-uuid')
        self.assertEqual(compact.iloc[0]['Bikes'],0)

    def test_low_percentage_does_not_turn_unknown_into_zero(self):
        frame=table_view([{'station_id':'A','station_name':'Library','low_sample_fraction':0.25},
                          {'station_id':'B','station_name':None,'low_sample_fraction':None}],'low')
        self.assertEqual(frame.iloc[0]['Low readings (%)'],25)
        self.assertTrue(pd.isna(frame.iloc[1]['Low readings (%)']))
        self.assertEqual(frame.iloc[1]['Station'],'B')
