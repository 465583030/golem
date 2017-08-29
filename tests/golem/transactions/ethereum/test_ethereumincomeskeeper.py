import mock
import random
import sys
import uuid

from golem.model import db
from golem import model
from golem import testutils
from golem.transactions.ethereum.ethereumincomeskeeper\
    import EthereumIncomesKeeper

from golem.ethereum.paymentprocessor import PaymentProcessor
from ethereum import tester

SQLITE3_MAX_INT = 2**31 - 1


def get_some_id():
    return str(uuid.uuid4())

def get_receiver_id():
    return '0x0000000000000000000000007d577a597b2742b498cb5cf0c26cdcd726d39e6e'

class TestEthereumIncomesKeeper(testutils.DatabaseFixture, testutils.PEP8MixIn):
    PEP8_FILES = [
        'golem/transactions/ethereum/ethereumincomeskeeper.py',
    ]


    def setUp(self, ):
        super(TestEthereumIncomesKeeper, self).setUp()
        random.seed()
        processor = mock.MagicMock()
        processor.eth_address.return_value = get_receiver_id()
        processor.synchronized.return_value = True
        self.instance = EthereumIncomesKeeper(processor)

    @mock.patch('golem.transactions.incomeskeeper.IncomesKeeper.received')
    def test_received(self, super_received_mock):
        received_kwargs = {
            'sender_node_id': get_some_id(),
            'task_id': get_some_id(),
            'subtask_id': get_some_id(),
            'transaction_id': get_some_id(),
            'block_number': random.randint(0, int(SQLITE3_MAX_INT / 2)),
            'value': random.randint(10, int(SQLITE3_MAX_INT / 2)),
        }

        # Transaction not in blockchain
        self.instance.processor.get_logs.return_value = None
        self.instance.received(**received_kwargs)
        super_received_mock.assert_not_called()

        # Payment for someone else
        self.instance.processor.get_logs.return_value = [
            {
                'topics': [
                    EthereumIncomesKeeper.LOG_ID,
                    get_some_id(),  # sender
                    get_some_id(),  # receiver
                ],
                'data': hex(random.randint(1, sys.maxsize)),
            },
        ]
        self.instance.received(**received_kwargs)
        super_received_mock.assert_not_called()
        super_received_mock.reset_mock()

        # Payment for us but value to small
        self.instance.processor.get_logs.return_value.append({
            'topics': [
                EthereumIncomesKeeper.LOG_ID,
                get_some_id(),  # sender
                self.instance.processor.eth_address(),  # receiver
            ],
            'data': hex(received_kwargs['value'] - 1),
        })
        self.instance.received(**received_kwargs)
        super_received_mock.assert_not_called()
        super_received_mock.reset_mock()

        # Payment with exact value
        self.instance.processor.get_logs.return_value.append({
            'topics': [
                EthereumIncomesKeeper.LOG_ID,
                get_some_id(),  # sender
                self.instance.processor.eth_address(),  # receiver
            ],
            'data': hex(1),
        })
        self.instance.received(**received_kwargs)
        super_received_mock.assert_called_once_with(**received_kwargs)
        super_received_mock.reset_mock()

        # Payment with higher value
        self.instance.processor.get_logs.return_value.append({
            'topics': [
                EthereumIncomesKeeper.LOG_ID,
                get_some_id(),  # sender
                self.instance.processor.eth_address(),  # receiver
            ],
            'data': hex(1),
        })
        self.instance.received(**received_kwargs)
        super_received_mock.assert_called_once_with(**received_kwargs)
        super_received_mock.reset_mock()

    # def test_transaction_overflow(self):
    #     received_kwargs = {
    #         'sender_node_id': get_some_id(),
    #         'task_id': get_some_id(),
    #         'subtask_id': 's1' + get_some_id()[:-2],
    #         'transaction_id': get_some_id(),
    #         'block_number': random.randint(0, int(SQLITE3_MAX_INT / 2)),
    #         'value': 2147483647,
    #     }
    #     self.instance.processor.get_logs.return_value = [
    #         {
    #             'topics': [
    #                 EthereumIncomesKeeper.LOG_ID,
    #                 get_some_id(),  # sender
    #                 self.instance.processor.eth_address(),  # receiver
    #             ],
    #             'data': hex(received_kwargs['value']),
    #         },
    #     ]
    #     with self.assertRaises(OverflowError):
    #         self.instance.received(**received_kwargs)

    def test_received_double_spending(self):
        received_kwargs = {
            'sender_node_id': get_some_id(),
            'task_id': get_some_id(),
            'subtask_id': 's1' + get_some_id()[:-2],
            'transaction_id': get_some_id(),
            'block_number': random.randint(0, int(SQLITE3_MAX_INT / 2)),
            'value': SQLITE3_MAX_INT - 1,
        }

        self.instance.processor.get_logs.return_value = [
            {
                'topics': [
                    EthereumIncomesKeeper.LOG_ID,
                    get_some_id(),  # sender
                    self.instance.processor.eth_address(),  # receiver
                ],
                'data': hex(received_kwargs['value']),
            },
        ]

        self.instance.received(**received_kwargs)


        # check the the income is in db
        with db.atomic():
            self.assertEqual(
                1,
                model.Income.select().where(
                    model.Income.subtask == received_kwargs['subtask_id']
                )
                    .count()
            )
            getincome = model.Income.get(sender_node=received_kwargs['sender_node_id'], task=received_kwargs['task_id'], subtask=received_kwargs['subtask_id'])
            self.assertEqual(getincome.value, received_kwargs['value'])
            self.assertEqual(getincome.transaction, received_kwargs['transaction_id'])
            self.assertEqual(getincome.block_number, received_kwargs['block_number'])

        # Try to use the same payment for another subtask
        received_kwargs['subtask_id'] = 's2' + get_some_id()[:-2]
        # Paranoid mode: Make sure subtask_id wasn't used before
        self.assertEqual(
            0,
            model.Income.select().where(
                model.Income.subtask == received_kwargs['subtask_id']
            )
            .count(),
            "Paranoid duplicated subtask check failed"
        )

        self.instance.received(**received_kwargs)
        self.assertEqual(
            0,
            model.Income.select().where(
                model.Income.subtask == received_kwargs['subtask_id']
            )
            .count()
        )
