import logging
from rest_framework import status
from rest_framework.response import Response
from rest_framework.exceptions import APIException, ValidationError, NotFound, PermissionDenied
from apps.audit.models import ServiceFailureLog

logger = logging.getLogger('lifegate')


def custom_exception_handler(exc, context):
    """
    Custom exception handler for DRF.
    """
    response = None
    
    if isinstance(exc, ValidationError):
        response = Response(
            {'error': 'Validation error', 'details': exc.detail},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    elif isinstance(exc, NotFound):
        response = Response(
            {'error': 'Resource not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    elif isinstance(exc, PermissionDenied):
        response = Response(
            {'error': 'Permission denied'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    elif isinstance(exc, APIException):
        response = Response(
            {'error': str(exc.detail)},
            status=exc.status_code
        )
    
    else:
        # Log unexpected errors
        logger.error(
            f"Unhandled exception: {str(exc)}",
            exc_info=True,
            extra={'context': context}
        )
        
        response = Response(
            {'error': 'Internal server error'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    return response


class ErrorHandlingMiddleware:
    """Middleware for global error handling."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        try:
            response = self.get_response(request)
            return response
        except Exception as e:
            logger.error(f"Middleware error: {str(e)}", exc_info=True)
            
            # Log service failure
            ServiceFailureLog.objects.create(
                service_type='APPLICATION',
                error_message=str(e),
                stack_trace=__import__('traceback').format_exc()
            )
            
            return Response(
                {'error': 'Internal server error'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SecurityMiddleware:
    """Security headers and encryption middleware."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Add security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        
        return response


class EncryptionMiddleware:
    """Middleware for encryption/decryption."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Add encryption context
        return self.get_response(request)


class RequestLoggingMiddleware:
    """Log all HTTP requests."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Log request
        logger.debug(
            f"{request.method} {request.path}",
            extra={
                'method': request.method,
                'path': request.path,
                'ip': self.get_client_ip(request),
            }
        )
        
        response = self.get_response(request)
        
        # Log response
        logger.debug(
            f"Response: {response.status_code}",
            extra={'status': response.status_code}
        )
        
        return response
    
    @staticmethod
    def get_client_ip(request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip