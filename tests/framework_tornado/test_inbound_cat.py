import json
from testing_support.fixtures import (make_cross_agent_headers,
        override_application_settings, validate_transaction_event_attributes,
        validate_transaction_metrics)

ENCODING_KEY = '1234567890123456789012345678901234567890'


_custom_settings = {
        'cross_process_id': '1#1',
        'encoding_key': ENCODING_KEY,
        'trusted_account_ids': [1],
        'cross_application_tracer.enabled': True,
        'distributed_tracing.enabled': False,
        'transaction_tracer.transaction_threshold': 0.0,
}


@override_application_settings(_custom_settings)
@validate_transaction_metrics('tornado.routing:_RoutingDelegate',
        rollup_metrics=[('ClientApplication/1#1/all', 1)])
@validate_transaction_event_attributes(
    required_params={'agent': [], 'user': [], 'intrinsic': []},
    forgone_params={'agent': [], 'user': [], 'intrinsic': []},
    exact_attrs={'agent': {}, 'user': {},
        'intrinsic': {'nr.referringTransactionGuid': 'b854df4feb2b1f06'}},
)
def test_inbound_cat_metrics_and_intrinsics(app):
    payload = ['b854df4feb2b1f06', False, '7e249074f277923d', '5d2957be']
    headers = make_cross_agent_headers(payload, ENCODING_KEY, '1#1')

    response = app.fetch('/simple', headers=headers)
    assert response.code == 200


@override_application_settings({
    'account_id': 1,
    'trusted_account_key': 1,
    'primary_application_id': 1,
    'distributed_tracing.enabled': True,
})
@validate_transaction_metrics(
    'tornado.routing:_RoutingDelegate',
    rollup_metrics=(
        ('Supportability/DistributedTrace/AcceptPayload/Success', 1),
    )
)
def test_inbound_dt(app):
    PAYLOAD = {
        "v": [0, 1],
        "d": {
            "ac": 1,
            "ap": 1,
            "id": "7d3efb1b173fecfa",
            "tx": "e8b91a159289ff74",
            "pr": 1.234567,
            "sa": True,
            "ti": 1518469636035,
            "tr": "d6b4ba0c3a712ca",
            "ty": "App"
        }
    }
    headers = {'newrelic': json.dumps(PAYLOAD)}
    response = app.fetch('/simple', headers=headers)
    assert response.code == 200
