import os
import sys
import time
import threading
import zlib
import base64
import traceback

import newrelic

import newrelic.lib.simplejson as simplejson

try:
    from collections import namedtuple
except:
    from newrelic.lib.namedtuple import namedtuple

from newrelic.api.transaction import Transaction
from newrelic.core.config import global_settings

# Class which is used to identify the point in the code where execution
# was occuring when the sample was taken. One of these will exist for
# each stack frame.
#
# Note that the UI was originally written based around the Java agent
# and for Java everything is in a class and so the class refers to the
# method data. For Python we only have access to the function name and
# do not know the class it is in. We thus only use the function name,
# but because in the case of a method of a class, there could be
# multiple methods of the same name defined within different classes, or
# even as a function at global scope, then we add the first line number
# of the code as part of the name. This makes it easier to identify
# which function or method it is rather than trying to work it out from
# execution line.

_MethodData = namedtuple('_MethodData',
        ['file_name', 'method_name', 'line_no'])

# Collection of the actual calls stacks is done by getting access to the
# current stack frames for all threads provided by CPython. If greenlets
# are used this is more complicated as greenlets will not appear in that
# list of stack frames. The stack frames for greenlets are available
# from the greenlet itself, but there is no list of all active greenlets.
# For now we attach the greenlet to the record of any transaction we
# are tracking and get to it that way. For convenience and because we
# need to refer to the transaction objects to categorise what the thread
# is being used for anyway, we put all the code dealing with that in the
# transaction class and we reach up and get the data on active threads
# from it.

USE_REAL_LINE_NUMBERS = True
ADD_REAL_LINE_LEAF_NODE = True
ADD_LINE_TO_FUNC_NAME = True
IGNORE_AGENT_FRAMES = True

AGENT_PACKAGE_DIRECTORY = os.path.dirname(newrelic.__file__) + '/'

def collect_stack_traces():
    """Collects representaions for the stack of each active thread
    within the process. A item is yielded for each thread which consists
    of a tuple with the category of thread and then the stack trace.

    """

    for thread_id, thread_category, frame in Transaction._active_threads():
        stack_trace = []

        # The initial stack frame is the lowest frame or leaf node and
        # we will track back up the stack. For that lowest stack frame
        # we optionally want to introduce a fake node which captures the
        # actual execution line so will be displayed as a separate item
        # in the UI. This makes it easier to identify where in the
        # actual code it was executing.

        leaf_node = ADD_REAL_LINE_LEAF_NODE

        while frame:
            # The value frame.f_code.co_firstlineno is the first line of
            # code in the file for the specified function. The value
            # frame.f_lineno is the actual line which is being executed
            # at the time the stack frame was being viewed.

            filename = frame.f_code.co_filename
            func_name = frame.f_code.co_name
            first_line = frame.f_code.co_firstlineno

            real_line = frame.f_lineno

            # As we experiment with representation of the stack trace in
            # UI, allow line number for each stack frame to either be
            # the actual execution line or start of function. It would
            # appear at this point it should always be execution line.

            line_no = real_line if USE_REAL_LINE_NUMBERS else first_line

            # Set ourselves up to process next frame back up the stack.

            frame = frame.f_back

            # So as to make it more obvious to the user as to what their
            # code is doing, we drop out stack frames related to the
            # agent instrumentation. Don't do this for the agent threads
            # though as we still need to seem them in that case so can
            # debug what the agent itself is doing.

            if IGNORE_AGENT_FRAMES and thread_category != 'AGENT':
                if filename.startswith(AGENT_PACKAGE_DIRECTORY):
                    continue

            if leaf_node:
                # Add the fake leaf node with line number of where the
                # code was executing at the point of the sample. This
                # could be actual Python code within the function, or
                # more likely showing the point where a call is being
                # made into a C function wrapped as Python object. The
                # latter can occur because we will not see stack frames
                # when calling into C functions.

                name = '@%s#%s' % (func_name, real_line)
                method_data = _MethodData(filename, name, line_no)
                stack_trace.append(method_data)

                leaf_node = False

            # Add the actual node for the function being called at this
            # level in the stack frames.

            if ADD_LINE_TO_FUNC_NAME:
                name = '%s#%s' % (func_name, first_line)
            else:
                name = func_name

            method_data = _MethodData(filename, name, line_no)

            stack_trace.append(method_data)

        yield thread_category, stack_trace

class ProfileNode(object):
    """This class provides the node used to construct the call tree.
    """
    def __init__(self, method_data, depth=1):
        self.method = method_data
        self.call_count = 0
        self.non_call_count = 0  # only used by Java, never updated for python
        self.children = {}   # key is _MethodData and value is ProfileNode
        self.depth = depth
        self.ignore = False

    def jsonable(self):
        """
        Return Serializable data for json.
        """
        return [self.method, self.call_count, self.non_call_count,
                [x for x in self.children.values() if not x.ignore]]

class ThreadProfiler(object):

    def __init__(self, profile_id, sample_period=0.1, duration=300,
            profile_agent_code=False):

        """Initialises the thread profiler but does not actually start
        the collection of samples. The start_profiling() method must be
        called separately.

        """

        # Sampling is performed from a background thread. Once started
        # it will continue for the nominated duration, take a sample and
        # then sleep for the duration of the specified sampling period.
        # Sleeping makes use of an event object so it can be interrupted
        # if an explicit instruction is received to stop the profiling
        # session from the UI.

        self._profiler_thread = threading.Thread(
                target=self._profiler_loop, name='NR-Profiler-Thread')
        self._profiler_thread.setDaemon(True)
        self._profiler_shutdown = threading.Event()

        self.profile_id = profile_id
        self.sample_period = sample_period
        self.duration = duration
        self.profile_agent_code = profile_agent_code

        self._sample_count = 0
        self._start_time = 0
        self._stop_time = 0

        # When collecting the call stack samples for where each thread
        # is executing, the data is aggregated into separate buckets
        # based on the categorisation of the thread type. Each bucket's
        # values is a dictionary that can hold multiple call trees. The
        # key is the method data and the value is the root of the call
        # tree, being an instance of a profile data node. A separate
        # node list is also kept for all instances of profile nodes so
        # can easily determine how many there are in total when done. If
        # over the the limit of how many nodes can be reported, the tree
        # will be pruned with least frequent visited nodes being
        # dropped.

        self._call_buckets = { 'REQUEST': {}, 'AGENT': {},
                'BACKGROUND': {}, 'OTHER': {} }
        self._node_list = []

    def _profiler_loop(self):
        """This is an infinite loop running in a background thread that
        periocally wakes up and collects the call stack samples.

        """

        # We collect one sample as soon as we start and then we sleep
        # for the time specified by the sampling period before taking
        # the next sample. We continue this until sampling is explicitly
        # stopped or until sleeping again would take us beyond the
        # overall duration specified for sampling.
	#
        # We bail out before the overall duration has expired so there
        # is a better chance of having data reported sooner. This is
        # because currently data is simply held in the application
        # object and reported on the next harvest. Because the sample
        # period typically divides evenly into the duration and the
        # duration is a multiple of the harvest duration, going long
        # means more likely to have to wait another minute before data
        # is reported to the data collector. Stopping short avoids us
        # needing to implement things so that the thread profiler itself
        # submits the profile data to the data collector if want to
        # ensure it gets there earlier.

        while True:
            if self._profiler_shutdown.isSet():
                return

            self._collect_sample()

            if self._stop_time - time.time() < self.sample_period:
                self.stop_profiling(wait_for_completion=False)
                return

            self._profiler_shutdown.wait(self.sample_period)

    def _collect_sample(self):
        """
        Collect stacktraces for each thread and update the appropriate call-
        tree bucket.
        """
        self._sample_count += 1
        for thread_category, stack_trace in collect_stack_traces():
            if thread_category is None:  # Thread category not found
                continue
            if (thread_category == 'AGENT') and (self.profile_agent_code == False):
                continue
            self._update_call_tree(self._call_buckets[thread_category], stack_trace)

    def _update_call_tree(self, bucket, stack_trace, depth=1):
        """
        Merge a stack trace to a call tree in the bucket. If no appropriate
        call tree is found then create a new call tree. An appropriate call
        tree will have the same root node as the last method in the stack
        trace. Methods from the stack trace are pulled from the end one at a
        time and merged with the call tree recursively.
        """

        if not stack_trace:
            return

        method = stack_trace.pop()
        call_tree = bucket.get(method)

        if call_tree is None:
            call_tree = ProfileNode(method, depth)
            self._node_list.append(call_tree)
            bucket[method] = call_tree

        call_tree.call_count += 1

        return self._update_call_tree(call_tree.children, stack_trace, depth+1)
    
    def start_profiling(self):
        self._start_time = time.time()
        self._stop_time = self._start_time + self.duration
        self._profiler_thread.start()

    def stop_profiling(self, wait_for_completion=False):
        self._stop_time = time.time()
        self._profiler_shutdown.set()

        if wait_for_completion:
            self._profiler_thread.join(self.sample_period)

    def profile_data(self):
        """
        Return the profile data once the thread profiler has finished otherwise 
        return None.
        """

        if self._profiler_thread.isAlive():
            return None

        call_data = {}
        thread_count = 0

        settings = global_settings()

        self._prune_call_trees(settings.agent_limits.thread_profiler_nodes)

        for thread_category, bucket in self._call_buckets.items():
            if bucket:
                call_data[thread_category] = bucket.values()
                thread_count += len(bucket)

        json_data = simplejson.dumps(call_data, default=lambda o: o.jsonable(),
                ensure_ascii=True, encoding='Latin-1',
                namedtuple_as_object=False)
        encoded_data = base64.standard_b64encode(zlib.compress(json_data))

        profile = [[self.profile_id, self._start_time*1000,
                self._stop_time*1000, self._sample_count, encoded_data,
                thread_count, 0]]

        return profile

    def _prune_call_trees(self, limit):
        """Prune the number of profile nodes we send up to the data
        collector down to the specified limit. Done to ensure not
        sending so much data that gets reject for being over size limit.

        """

        if len(self._node_list) <= limit:
            return

        # We sort the profile nodes based on call count, but also take
        # into consideration the depth of the node in the call tree.
        # Based on sort order, we then ignore any nodes over our limit.
        #
        # We include depth as that way we try and trim the deepest and
        # least visited leaf nodes first. If we don't do this, then
        # depending on how sorting orders nodes with same call count, we
        # could ignore a parent node high up in call chain even though
        # children weren't being ignored and so effectively ignore more
        # than the minimum we need to. Granted this would only occur
        # where was a linear call tree where all had the same call count,
        # such as may occur with recursion.
	#
        # Also note that we still can actually end up with less nodes in
        # the end being displayed in the UI than the limit being applied
        # even though we initially cutoff at the limit. This is because
        # we are looking at nodes from different categories before they
        # have been merged together. If a node appears at same relative
        # position in multiple categories, then when displaying multiple
        # categories in UI, the duplicates only appear as one after the
        # UI merges them.

        self._node_list.sort(key=lambda x: (x.call_count, -x.depth),
                reverse=True)

        for node in self._node_list[limit:]:
            node.ignore = True

def fib(n):
    """
    Test recursive function. 
    """
    if n < 2:
        return n
    return fib(n-1) + fib(n-2)

if __name__ == "__main__":
    t = ThreadProfiler(-1, 0.1, 1, profile_agent_code=True)
    t.start_profiling()
    #fib(35)
    import time
    time.sleep(1.1)
    c = zlib.decompress(base64.standard_b64decode(t.profile_data()[0][4]))
    print c
    #print ProfileNode.node_count
