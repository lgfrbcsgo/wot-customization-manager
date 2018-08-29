import sys
import inspect
import adisp
from functools import wraps
from threading import RLock
from debug_utils import LOG_CURRENT_EXCEPTION


def block_concurrent(gen):
    outer = {'running': False}
    lock = RLock()

    @wraps(gen)
    def ensure_wrapper(*args, **kwargs):
        lock.acquire()
        if outer['running']:
            lock.release()
            return
        outer['running'] = True
        lock.release()
        try:
            for val in gen(*args, **kwargs):
                yield val
        finally:
            outer['running'] = False

    return ensure_wrapper


def generic_hook(*decorators):
    def hook(hook_handler):
        def build_decorator(module, func_name):
            def decorator(func):
                orig_func = getattr(module, func_name)

                def func_wrapper(*args, **kwargs):
                    return hook_handler(orig_func, func, *args, **kwargs)

                for dec in decorators + (wraps(orig_func),):
                    func_wrapper = dec(func_wrapper)

                if inspect.ismodule(module):
                    setattr(sys.modules[module.__name__], func_name, func_wrapper)
                elif inspect.isclass(module):
                    setattr(module, func_name, func_wrapper)

                return func
            return decorator
        return build_decorator
    return hook


@generic_hook()
def run_before(orig_func, func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except:
        LOG_CURRENT_EXCEPTION()
    finally:
        return orig_func(*args, **kwargs)


@generic_hook(adisp.process)
def run_before_async(orig_func, func, *args, **kwargs):
    try:
        for val in func(*args, **kwargs):
            yield val
    except:
        LOG_CURRENT_EXCEPTION()
    finally:
        orig_func(*args, **kwargs)
