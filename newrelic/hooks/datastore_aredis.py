# Copyright 2010 New Relic, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import re

from newrelic.api.datastore_trace import DatastoreTrace
from newrelic.api.transaction import current_transaction
from newrelic.common.object_wrapper import wrap_function_wrapper

_aredis_client_methods = (
    "bgrewriteaof",
    "bgsave",
    "client_kill",
    "client_list",
    "client_getname",
    "client_setname",
    "config_get",
    "config_set",
    "config_resetstat",
    "config_rewrite",
    "dbsize",
    "debug_object",
    "echo",
    "flushall",
    "flushdb",
    "info",
    "lastsave",
    "object",
    "ping",
    "save",
    "sentinel",
    "sentinel_get_master_addr_by_name",
    "sentinel_master",
    "sentinel_masters",
    "sentinel_monitor",
    "sentinel_remove",
    "sentinel_sentinels",
    "sentinel_set",
    "sentinel_slaves",
    "shutdown",
    "slaveof",
    "slowlog_get",
    "slowlog_reset",
    "time",
    "append",
    "bitcount",
    "bitop",
    "bitpos",
    "decr",
    "delete",
    "dump",
    "exists",
    "expire",
    "expireat",
    "get",
    "getbit",
    "getrange",
    "getset",
    "incr",
    "incrby",
    "incrbyfloat",
    "keys",
    "mget",
    "mset",
    "msetnx",
    "move",
    "persist",
    "pexpire",
    "pexpireat",
    "psetex",
    "pttl",
    "randomkey",
    "rename",
    "renamenx",
    "restore",
    "set",
    "setbit",
    "setex",
    "setnx",
    "setrange",
    "strlen",
    "substr",
    "ttl",
    "type",
    "watch",
    "unwatch",
    "blpop",
    "brpop",
    "brpoplpush",
    "lindex",
    "linsert",
    "llen",
    "lpop",
    "lpush",
    "lpushx",
    "lrange",
    "lrem",
    "lset",
    "ltrim",
    "rpop",
    "rpoplpush",
    "rpush",
    "rpushx",
    "sort",
    "scan",
    "scan_iter",
    "sscan",
    "sscan_iter",
    "hscan",
    "hscan_inter",
    "zscan",
    "zscan_iter",
    "sadd",
    "scard",
    "sdiff",
    "sdiffstore",
    "sinter",
    "sinterstore",
    "sismember",
    "smembers",
    "smove",
    "spop",
    "srandmember",
    "srem",
    "sunion",
    "sunionstore",
    "zadd",
    "zcard",
    "zcount",
    "zincrby",
    "zinterstore",
    "zlexcount",
    "zrange",
    "zrangebylex",
    "zrangebyscore",
    "zrank",
    "zrem",
    "zremrangebylex",
    "zremrangebyrank",
    "zremrangebyscore",
    "zrevrange",
    "zrevrangebyscore",
    "zrevrank",
    "zscore",
    "zunionstore",
    "pfadd",
    "pfcount",
    "pfmerge",
    "hdel",
    "hexists",
    "hget",
    "hgetall",
    "hincrby",
    "hincrbyfloat",
    "hkeys",
    "hlen",
    "hset",
    "hsetnx",
    "hmset",
    "hmget",
    "hvals",
    "publish",
    "eval",
    "evalsha",
    "script_exists",
    "script_flush",
    "script_kill",
    "script_load",
    "setex",
    "lrem",
    "zadd",
)

_aredis_multipart_commands = set(["client", "cluster", "command", "config", "debug", "sentinel", "slowlog", "script"])

_aredis_operation_re = re.compile(r"[-\s]+")


def _conn_attrs_to_dict(connection):
    return {
        "host": getattr(connection, "host", None),
        "port": getattr(connection, "port", None),
        "path": getattr(connection, "path", None),
        "db": getattr(connection, "db", None),
    }


def _instance_info(kwargs):
    host = kwargs.get("host") or "localhost"
    port_path_or_id = str(kwargs.get("port") or kwargs.get("path", "unknown"))
    db = str(kwargs.get("db") or 0)

    return (host, port_path_or_id, db)


def _wrap_Aredis_method_wrapper_(module, instance_class_name, operation):
    def _nr_wrapper_Aredis_method_(wrapped, instance, args, kwargs):
        transaction = current_transaction()
        if transaction is None:
            return wrapped(*args, **kwargs)

        dt = DatastoreTrace(product="Aredis", target=None, operation=operation)
        
        transaction._nr_datastore_instance_info = (None, None, None)

        with dt:
            result = wrapped(*args, **kwargs)

            host, port_path_or_id, db = transaction._nr_datastore_instance_info
            dt.host = host
            dt.port_path_or_id = port_path_or_id
            dt.database_name = db
            return result

    name = "%s.%s" % (instance_class_name, operation)
    wrap_function_wrapper(module, name, _nr_wrapper_Aredis_method_)


def instrument_aredis_client(module):
    if hasattr(module, "StrictRedis"):
        for name in _aredis_client_methods:
            if hasattr(module.StrictRedis, name):
            #if name in vars(module.StrictRedis)["RESPONSE_CALLBACKS"]:
                _wrap_Aredis_method_wrapper_(module, "StrictRedis", name)
    
    if hasattr(module, "StrictRedisCluster"):
        for name in _aredis_client_methods:
            if hasattr(module.StrictRedisCluster, name):
            #if name in vars(module.StrictRedis)["RESPONSE_CALLBACKS"]:
                _wrap_Aredis_method_wrapper_(module, "StrictRedisCluster", name)

