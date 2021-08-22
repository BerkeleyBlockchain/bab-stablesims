""" Retrieve ETH gas fees during Black Thursday
    Instructions:
    From project root, run this file with the following env variables:
    ELASTIC_EMAIL="[email]"
    ELASTIC_AUTH="[auth_key]"
    filename="[filename]"
    start="[UNIX timestamp]"
    end="[UNIX timestamp]"
    python3 price_feeds/gas/get_gas.py
"""


import os
import sys

import pandas as pd
from elasticsearch import Elasticsearch


# Initialize the ElasticSearch Client
def initialize_elastic(network):

    es = Elasticsearch(
        hosts=[network],
        http_auth=(os.getenv("ELASTIC_EMAIL"), os.getenv("ELASTIC_AUTH")),
        timeout=180,
    )
    return es


# Create a List with the Networks we use for this analysis:

networks = [
    "https://api.anyblock.tools/ethereum/ethereum/mainnet/es/",
    "https://api.anyblock.tools/poa/xdai/es/",
    "https://api.anyblock.tools/ewf/ewc/es/",
    "https://api.anyblock.tools/ethereum/classic/mainnet/es",
]

# define a function to fetch daily percentiles for the past 2 years


def fetch_gas_day(network, start, end):
    es = initialize_elastic(network)
    return es.search(
        index="tx",
        doc_type="tx",
        # search_type="scan",
        body={
            "_source": ["timestamp", "gasPrice.num"],
            "query": {
                "bool": {"must": [{"range": {"timestamp": {"gte": start, "lt": end}}}],}
            },
            "aggs": {
                "hour_bucket": {
                    "date_histogram": {
                        "field": "timestamp",
                        "interval": "10m",
                        "format": "yyyy-MM-dd hh:mm:ss",
                    },
                    "aggs": {
                        "avgGasDay": {"avg": {"field": "gasPrice.num"}},
                        "percentilesDay": {
                            "percentiles": {
                                "field": "gasPrice.num",
                                "percents": [35, 60, 90, 100],
                            }
                        },
                    },
                }
            },
            "size": 0,
        },
    )


# Get the Aggregation results of the Query into a DataFrame


def query_to_dataframe(data):
    combine = []

    for i in data["aggregations"].values():
        for j in i.values():
            for k in j:
                combine.append(k)
    dfName = pd.DataFrame(combine)
    combine = None
    return dfName


DEFAULT = object()  # creating for a default value the user would not
# be expected to pass

# after we use the function query_to_dataframe, our dataframe
# has columns with dictionaries in it:
# The Values for the Column avgGasMin is:
# {'value': FLOAT_Value} -> therefore we drop the dictionary to get
# the raw value into the column.
# Same for Percentiles..


def concat_dictionaries_df(
    df,
    drop_a="avgGasMin",
    drop_b="value",
    drop_c="percentilesMin",
    drop_d="values",
    symbols=DEFAULT,
):
    # dropping avgGasMin means we get a "value" column
    df = pd.concat([df.drop([drop_a], axis=1), df[drop_a].apply(pd.Series)], axis=1)

    # dropping value column with dictionary so we get the raw values
    df = pd.concat([df.drop([drop_b], axis=1), df[drop_b].apply(pd.Series)], axis=1)

    # do the same for percentilesMin...
    df = pd.concat([df.drop([drop_c], axis=1), df[drop_c].apply(pd.Series)], axis=1)

    df = pd.concat([df.drop([drop_d], axis=1), df[drop_d].apply(pd.Series)], axis=1)

    if symbols is DEFAULT:
        pass
    else:
        df.columns = symbols
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    df.head()
    return df


def get_gas_price_day(start, end):
    eth_day = fetch_gas_day(networks[0], start, end)
    df_gas_day = query_to_dataframe(eth_day)

    # Column Names for Day Df
    symbols_day = [
        "datetime",
        "timestamp",
        "doc_count",
        "avgGasDay",
        "30.0 Perc",
        "60.0 Perc",
        "90.0 Perc",
        "100.0 Perc",
    ]

    ## Concat the wrong Columns which are Dictionaries,rename Columns and finish dataset

    df_all_day = concat_dictionaries_df(
        df_gas_day,
        drop_a="avgGasDay",
        drop_b="value",
        drop_c="percentilesDay",
        drop_d="values",
        symbols=symbols_day,
    )
    return df_all_day


if __name__ == "__main__":
    file = os.getenv("filename")
    start_timestamp = os.getenv("start")
    end_timestamp = os.getenv("end")

    if not (file or start_timestamp or end_timestamp):
        print("Please enter filename or start/end timestamps")
        sys.exit()
    df_day = get_gas_price_day(start_timestamp, end_timestamp)
    df_day.to_json(f"price_feeds/gas/{file}.json", orient="table", index=False)
