
from hexbytes import HexBytes
from ocean_lib.models.bpool import BPool
from ocean_lib.ocean.util import from_base_18, to_base_18
from scipy.interpolate import interp1d
from web3.datastructures import AttributeDict


def get_pool_db_mapping():
    mapping = {
      "settings": {
        "analysis": {
          "normalizer": {
            "ocean_normalizer": {
              "type": "custom",
              "char_filter": [],
              "filter": [
                "lowercase",
                "asciifolding"
              ]
            }
          }
        }
      },
      "mappings": {
        "_doc": {
          "properties": {
            "id": {
              "type": "text",
              "fields": {
                "keyword": {
                  "type": "keyword",
                  "ignore_above": 256
                }
              }
            }
          }
        }
      }
    }
    return mapping


def transform_event_logs(logs):
    tlogs = []
    for l in logs:
        tl = {}
        for key, value in l.items():
            if isinstance(value, HexBytes):
                value = value.hex()
            elif isinstance(value, AttributeDict):
                value = dict(value)

            if key == 'args':
                for _key in value.keys():
                    if isinstance(value[_key], int):
                        value[_key] = str(value[_key])

            tl[key] = value

        tlogs.append(tl)
    return tlogs


def get_liquidity_logs(ocean, dao, pool_address, pool_info=None, save=True, from_block=None, to_block=None, dt_address=None):
    ocean_pool = ocean.pool
    web3 = ocean.web3
    pool = pool_info if pool_info else dao.get_pool(pool_address)
    join_records = pool.get('LOG_JOIN', [])
    exit_records = pool.get('LOG_EXIT', [])
    swap_records = pool.get('LOG_SWAP', [])
    last_processed_block = pool.get('liquidityLastProcessedBlock', 0)
    if not to_block:
        to_block = web3.eth.blockNumber

    if not last_processed_block:
        if not from_block:
            from_block = ocean_pool.get_creation_block(pool_address)
    else:
        from_block = last_processed_block + 1

    if (to_block - from_block) < 2:
        return join_records, exit_records, swap_records

    _join = [dict(o) for o in ocean_pool.get_all_liquidity_additions(web3, pool_address, from_block, to_block=to_block, token_address=dt_address)]
    _exit = [dict(o) for o in ocean_pool.get_all_liquidity_removals(web3, pool_address, from_block, to_block=to_block, token_address=dt_address)]
    _swap = [dict(o) for o in ocean_pool.get_all_swaps(web3, pool_address, from_block, to_block=to_block, token_address=dt_address)]
    _join = transform_event_logs(_join)
    _exit = transform_event_logs(_exit)
    _swap = transform_event_logs(_swap)

    _lastblock = from_block
    _first_block = to_block

    def _update_records_list(records, new_records, first_block, last_block):
        if new_records:
            first_block = min(new_records[0]['blockNumber'], first_block)
            last_block = max(new_records[-1]['blockNumber'], last_block)
            records.extend(new_records)
            return first_block, last_block, sorted({r['blockNumber'] for r in new_records})
        return first_block, last_block, []

    def _update_timestamps(records, interpolate_f, times=None):
        if records:
            if interpolate_f:
                times = interpolate_f([r['blockNumber'] for r in records])
            for i, r in enumerate(records):
                r['timestamp'] = times[i]

    _first_block, _lastblock, _jblocks = _update_records_list(join_records, _join, _first_block, _lastblock)
    _first_block, _lastblock, _eblocks = _update_records_list(exit_records, _exit, _first_block, _lastblock)
    _first_block, _lastblock, _sblocks = _update_records_list(swap_records, _swap, _first_block, _lastblock)

    if _join or _exit or _swap:
        timestamps = []
        _blocks = []
        all_blocks = sorted(set(_jblocks + _eblocks + _sblocks))
        # if (_lastblock - _first_block) > 4000:
        #     # get timestamps for blocknumber every 4 hours, assuming there is
        #     # 240 blocks/hour (on average) or 15 seconds block time..
        #     # use interpolation to fill in the other timestamps
        #     # 960 = 240 * 4
        #     _blocks = list(range(_first_block, _lastblock, 960)) + [_lastblock]
        #     for b in _blocks:
        #         timestamps.append(web3.eth.getBlock(b).timestamp)
        #
        # else:

        step = int(len(all_blocks) / 7) if len(all_blocks) > 14 else 1
        blocks_i = list(range(0, len(all_blocks), step)) + [len(all_blocks)-1]
        if blocks_i[-1] == blocks_i[-2]:
            blocks_i = blocks_i[:-1]
        for b in sorted({all_blocks[i] for i in blocks_i}):
            _blocks.append(b)
            timestamps.append(web3.eth.getBlock(b).timestamp)

        f = None
        if len(_blocks) > 2:
            f = interp1d(_blocks, timestamps)
        _update_timestamps(_join, f, timestamps)
        _update_timestamps(_exit, f, timestamps)
        _update_timestamps(_swap, f, timestamps)

        pool['LOG_JOIN'] = join_records
        pool['LOG_EXIT'] = exit_records
        pool['LOG_SWAP'] = swap_records
        pool['liquidityLastProcessedBlock'] = to_block
        if save and (_join or _exit or _swap):
            dao.update_dt_pool_data(pool_address, pool)

    return join_records, exit_records, swap_records


def process_liquidity_logs(join_records, exit_records, swap_records, ocean_address, include_swaps=True):
    def from18(value):
        return from_base_18(int(value))

    # Liquidity Additions
    ocn_liq_add_list = [(from18(r['args']['tokenAmountIn']), r['timestamp'])
                        for r in join_records if r['args']['tokenIn'] == ocean_address]
    dt_liq_add_list = [(from18(r['args']['tokenAmountIn']), r['timestamp'])
                       for r in join_records if r['args']['tokenIn'] != ocean_address]

    # Liquidity removals
    ocn_liq_rem_list = [(from18(r['args']['tokenAmountOut']), r['timestamp'])
                        for r in exit_records if r['args']['tokenOut'] == ocean_address]
    dt_liq_rem_list = [(from18(r['args']['tokenAmountOut']), r['timestamp'])
                       for r in exit_records if r['args']['tokenOut'] != ocean_address]

    if include_swaps:
        # l.args.caller, l.args.tokenIn, l.args.tokenAmountIn, l.args.tokenOut, l.args.tokenAmountOut,
        # l.blockNumber, l.transactionHash
        for r in swap_records:
            timestamp = r['timestamp']
            if r['args']['tokenIn'] == ocean_address:
                # ocn is the tokenIn
                ocn_liq_add_list.append((from18(r['args']['tokenAmountIn']), timestamp))
                dt_liq_rem_list.append((from18(r['args']['tokenAmountOut']), timestamp))
            else:  # ocn is the tokenOut
                ocn_liq_rem_list.append((from18(r['args']['tokenAmountOut']), timestamp))
                dt_liq_add_list.append((from18(r['args']['tokenAmountIn']), timestamp))

    return ocn_liq_add_list, ocn_liq_rem_list, dt_liq_add_list, dt_liq_rem_list


def get_liquidity_history(ocean, dao, pool_address, from_block=None, to_block=None, dt_address=None):
    join_records, exit_records, swap_records = get_liquidity_logs(
        ocean, dao, pool_address, from_block=from_block, to_block=to_block, dt_address=dt_address
    )

    ocn_address = ocean.pool.ocean_address
    (ocn_liq_add_list, ocn_liq_rem_list,
     dt_liq_add_list, dt_liq_rem_list) = process_liquidity_logs(
        join_records, exit_records, swap_records, ocn_address
    )

    ocn_liq_rem_list = [(-v, t) for v, t in ocn_liq_rem_list]
    dt_liq_rem_list = [(-v, t) for v, t in dt_liq_rem_list]

    ocn_add_remove_list = ocn_liq_add_list + ocn_liq_rem_list
    ocn_add_remove_list.sort(key=lambda x: x[1])

    dt_add_remove_list = dt_liq_add_list + dt_liq_rem_list
    dt_add_remove_list.sort(key=lambda x: x[1])

    return ocn_add_remove_list, dt_add_remove_list


def get_token_price(pool, balance_in, weight_in, balance_out, weight_out, swap_fee):
    in_amount = pool.calcInGivenOut(
        to_base_18(balance_in),
        to_base_18(weight_in),
        to_base_18(balance_out),
        to_base_18(weight_out),
        to_base_18(1.0),
        to_base_18(swap_fee)
    )
    return from_base_18(in_amount)


def get_pool_info(ocean, dao, pool_address, dt_address=None, from_block=None, to_block=None):
    pool = get_pool(dao, pool_address)
    if not pool:
        bpool = BPool(pool_address)
        pool = ocean.pool.get_pool_info(
            pool_address, dt_address=dt_address,
            from_block=from_block, to_block=to_block,
            flags=['reserve']
        )
        pool['swapFee'] = from_base_18(bpool.getSwapFee())
        if not dt_address:
            dt_address = pool['dataTokenAddress']

        pool['spotPrice1DT'] = from_base_18(bpool.getSpotPrice(ocean.OCEAN_address, dt_address)),
        pool['totalPrice1DT'] = get_token_price(
            bpool, pool['oceanReserve'], pool['oceanWeight'], pool['dtReserve'], pool['dtWeight'], pool['swapFee'])

        dao.update_dt_pool_data(pool_address, pool)
        # keys = {
        #     'address',
        #     'dataTokenAddress',
        #     'spotPrice1DT',
        #     'totalPrice1DT',
        #     'oceanWeight',
        #     'oceanReserve',
        #     'dtWeight',
        #     'dtReserve',
        #     'totalOceanAdditions',
        #     'totalOceanRemovals',
        #     'LOG_JOIN',
        #     'LOG_EXIT',
        #     'LOG_SWAP'
        # }

    from_block = pool['fromBlockNumber']
    to_block = pool['latestBlockNumber']
    join_records, exit_records, swap_records = get_liquidity_logs(
        ocean, dao, pool_address, pool,
        save=True, from_block=from_block,
        to_block=to_block, dt_address=dt_address
    )

    liq_lists = process_liquidity_logs(join_records, exit_records, swap_records, ocean.OCEAN_address, include_swaps=False)
    (ocn_liq_add_list, ocn_liq_rem_list, dt_liq_add_list, dt_liq_rem_list) = liq_lists

    total_ocn_additions = sum(r[0] for r in ocn_liq_add_list)
    total_ocn_removals = sum(r[0] for r in ocn_liq_rem_list)
    pool.update({
        'totalOceanAdditions': total_ocn_additions,
        'totalOceanRemovals': total_ocn_removals,
    })

    return pool


def get_pool(dao, pool_address):
    try:
        pool = dao.get_pool(pool_address)
    except Exception:
        pool = {}

    return pool


# build accumulated liquidity
def get_accumulative_values(values_list):
    acc_values = [values_list[0]]
    n = 0
    for k, (v, t) in enumerate(values_list[1:]):
        if acc_values[n][1] == t:
            acc_values[n] = (acc_values[n][0] + v, t)
        else:
            acc_values.append((acc_values[n][0] + v, t))
            n += 1
    return acc_values


def build_liquidity_and_price_history(ocn_liquidity_changes, dt_liquidity_changes, ocn_weight, dt_weight, swap_fee):
    # numer = ov / ocn_weight
    # denom = dtv / dt_weight
    # ratio = numer / denom
    scale = 1.0 / (1.0 - swap_fee)
    # # price = ratio * scale
    weight_ratio = dt_weight / ocn_weight
    tot_ratio = weight_ratio * scale
    # p = ((ov / ocn_weight) / (dtv / dt_weight)) * (1.0 / (1.0 - swap_fee))
    # uint numer = bdiv(tokenBalanceIn, tokenWeightIn);
    # uint denom = bdiv(tokenBalanceOut, tokenWeightOut);
    # uint ratio = bdiv(numer, denom);
    # uint scale = bdiv(BONE, bsub(BONE, swapFee));
    # return  (spotPrice = bmul(ratio, scale));

    accumulated_ocn_values = get_accumulative_values(ocn_liquidity_changes)
    accumulated_dt_values = get_accumulative_values(dt_liquidity_changes)

    _ocn_values = []
    _dt_values = []
    prices = []
    all_times = sorted({tup[1] for tup in (accumulated_dt_values + accumulated_ocn_values)})

    i = 0
    j = 0
    ocnv, ocnt = accumulated_ocn_values[i]
    dtv, dtt = accumulated_dt_values[j]
    ocn_l = len(accumulated_ocn_values)
    dt_l = len(accumulated_dt_values)
    assert ocnt == dtt, 'The first timestamp does not match between ocean and datatoken liquidity.'
    assert all_times[0] == ocnt, ''

    for t in all_times:
        if (i+1) < ocn_l:
            _v, _t = accumulated_ocn_values[i + 1]
            if _t <= t:
                i += 1
                ocnv = _v

        if (j+1) < dt_l:
            _v, _t = accumulated_dt_values[j + 1]
            if _t <= t:
                j += 1
                dtv = _v

        _ocn_values.append((ocnv, t))
        _dt_values.append((dtv, t))
        prices.append(((ocnv / dtv) * tot_ratio, t))

    return _ocn_values, _dt_values, prices
