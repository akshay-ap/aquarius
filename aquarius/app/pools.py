import json
import logging
import os

from flask import Blueprint, request, Response

from ocean_lib.config_provider import ConfigProvider
from ocean_lib.ocean.ocean import Ocean

import aquarius.app.pool_helper as pool_helper
from aquarius.app.dao import Dao
from aquarius.app.util import get_request_data

pools = Blueprint('pools', __name__)

logger = logging.getLogger(__name__)


@pools.route('/history/<poolAddress>', methods=['GET'])
def get_liquidity_history(poolAddress):
    """

    :param poolAddress:
    :return: json object with two keys: `ocean` and `datatoken`
      each has a list of datapoints sampled at specific time intervals from the pools liquidity history.
    """
    try:
        result = dict()
        data = get_request_data(request) or {}
        dt_address = data.get('datatokenAddress', None)

        ocean = Ocean(ConfigProvider.get_config())
        dao = Dao()
        if not dt_address:
            dt_address = ocean.pool.get_token_address(poolAddress, validate=False)

        pool_data = pool_helper.get_pool_info(ocean, dao, poolAddress, dt_address)
        swap_fee = pool_data['swapFee']
        ocn_weight = pool_data['oceanWeight']
        dt_weight = pool_data['dtWeight']

        ocn_add_remove_list, dt_add_remove_list = pool_helper.get_liquidity_history(ocean, dao, poolAddress)
        ocn_add_remove_list = [(v, int(t)) for v, t in ocn_add_remove_list]
        dt_add_remove_list = [(v, int(t)) for v, t in dt_add_remove_list]

        ocn_reserve_history, dt_reserve_history, price_history = pool_helper.build_liquidity_and_price_history(
            ocn_add_remove_list, dt_add_remove_list, ocn_weight, dt_weight, swap_fee
        )

        result['oceanAddRemove'] = ocn_add_remove_list
        result['datatokenAddRemove'] = dt_add_remove_list
        result['oceanReserveHistory'] = ocn_reserve_history
        result['datatokenReserveHistory'] = dt_reserve_history
        result['datatokenPriceHistory'] = price_history
        return Response(json.dumps(result), 200, content_type='application/json')
    except Exception as e:
        logger.error(f'pools/history/{poolAddress}: {str(e)}', exc_info=1)
        return f'Get pool liquidity/price history failed: {str(e)}', 500


@pools.route('/liquidity/<poolAddress>', methods=['GET'])
def get_current_liquidity_stats(poolAddress):
    """

    :param poolAddress:
    :return:
    """
    try:
        data = get_request_data(request) or {}
        dt_address = data.get('datatokenAddress', None)
        from_block = data.get('fromBlock', None)
        to_block = data.get('toBlock', None)
        complete_info = int(data.get('includeAllPoolInfo', '0'))
        ocean = Ocean(ConfigProvider.get_config())
        if complete_info:
            pool_info = ocean.pool.get_pool_info(poolAddress, dt_address, from_block, to_block)
        else:
            dao = Dao()
            pool_info = pool_helper.get_pool_info(ocean, dao, poolAddress, from_block, to_block)
            pool_info.pop('LOG_JOIN')
            pool_info.pop('LOG_EXIT')
            pool_info.pop('LOG_SWAP')

        return Response(json.dumps(pool_info), 200, content_type='application/json')

    except Exception as e:
        logger.error(f'pools/liquidity/{poolAddress}: {str(e)}')
        return f'Get pool current liquidity stats failed: {str(e)}', 500


@pools.route('/user/<userAddress>', methods=['GET'])
def get_user_balances(userAddress):
    """

    :param userAddress:
    :return:
    """
    try:
        data = get_request_data(request) or {}
        from_block = data.get('fromBlock', int(os.getenv('BFACTORY_BLOCK', 0)))
        ocean = Ocean(ConfigProvider.get_config())
        result = ocean.pool.get_user_balances(userAddress, from_block)
        return Response(json.dumps(result), 200, content_type='application/json')
    except Exception as e:
        logger.error(f'pools/user/{userAddress}: {str(e)}')
        return f'Get pool user balances failed: {str(e)}', 500
