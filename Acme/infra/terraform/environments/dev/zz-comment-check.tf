# Throwaway (PR #10, never merged): exercises the plan comment's failed path via the Conftest deny.
resource "aws_security_group" "comment_check" {
  name   = "comment-check"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
}
