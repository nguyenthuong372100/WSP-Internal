from odoo import models, fields, api
import logging
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    worked_hours = fields.Float(string="Total Worked Hours", readonly=True)
    attendance_line_ids = fields.One2many(
        'hr.payslip.attendance',
        'payslip_id',
        string="Attendance Records",
        copy=False
    )

    def _compute_total_worked_hours(self):
        """
        Tính tổng số giờ làm việc từ các bản ghi attendance đã được phê duyệt.
        """
        for payslip in self:
            total_hours = sum(
                line.worked_hours for line in payslip.attendance_line_ids if line.approved
            )
            payslip.worked_hours = total_hours

    @api.onchange('employee_id', 'date_from', 'date_to')
    def _onchange_attendance_records(self):
        if self.employee_id and self.date_from and self.date_to:
            # Xóa bản ghi cũ an toàn
            if self.attendance_line_ids:
                self.attendance_line_ids.unlink()

            # Lấy bản ghi attendance liên quan
            attendances = self.env['hr.attendance'].search([
                ('employee_id', '=', self.employee_id.id),
                ('check_in', '>=', self.date_from),
                ('check_out', '<=', self.date_to),
            ])

            # Tạo bản ghi mới
            self.attendance_line_ids = [
                (0, 0, {
                    'attendance_id': attendance.id,
                    'check_in': attendance.check_in,
                    'check_out': attendance.check_out,
                    'worked_hours': attendance.worked_hours,
                    'approved': False,
                })
                for attendance in attendances
            ]
    def write(self, vals):
        """
        Override phương thức write để đảm bảo dữ liệu attendance không bị mất.
        """
        result = super(HrPayslip, self).write(vals)
        for payslip in self:
            if any(key in vals for key in ['employee_id', 'date_from', 'date_to']):
                payslip._onchange_attendance_records()
        return result

    @api.model
    def create(self, vals):
        """
        Override phương thức create để tạo các bản ghi attendance khi tạo payslip.
        """
        payslip = super(HrPayslip, self).create(vals)
        if 'employee_id' in vals and 'date_from' in vals and 'date_to' in vals:
            payslip._onchange_attendance_records()
        return payslip

    def action_duplicate_payslips(self):
        for payslip in self:
            # Tính toán ngày mới
            new_start_date = payslip.date_from + relativedelta(months=1)
            new_end_date = payslip.date_to + relativedelta(months=1)

            # Kiểm tra nếu đã tồn tại payslip với employee và khoảng ngày trùng
            existing_payslip = self.env['hr.payslip'].search([
                ('employee_id', '=', payslip.employee_id.id),
                ('date_from', '=', new_start_date),
                ('date_to', '=', new_end_date),
            ], limit=1)

            if existing_payslip:
                raise UserError((
                    f"Payslip đã tồn tại cho nhân viên {payslip.employee_id.name} "
                    f"từ ngày {new_start_date} đến ngày {new_end_date}. Không thể duplicate!"
                ))

            # Sao chép payslip hiện tại với start_date và end_date mới
            new_payslip = payslip.copy({
                'date_from': new_start_date,
                'date_to': new_end_date,
            })

            # Xóa các bản ghi attendance cũ liên quan đến payslip mới (nếu có)
            if new_payslip.attendance_line_ids:
                new_payslip.attendance_line_ids.unlink()

            # Lọc lại các bản ghi attendance report theo khoảng ngày mới
            attendances = self.env['hr.attendance'].search([
                ('employee_id', '=', payslip.employee_id.id),
                ('check_in', '>=', new_start_date),
                ('check_out', '<=', new_end_date),
            ])

            # Tạo bản ghi attendance liên kết với payslip mới
            new_payslip.attendance_line_ids = [
                (0, 0, {
                    'attendance_id': attendance.id,
                    'check_in': attendance.check_in,
                    'check_out': attendance.check_out,
                    'worked_hours': attendance.worked_hours,
                    'approved': False,
                })
                for attendance in attendances
            ]
            
            # Bỏ trạng thái readonly cho các trường date_from và date_to
            # new_payslip.write({
            #     'state': 'draft'
            # })

    def action_approve_attendance(self):
            pass
        
class HrPayslipAttendance(models.Model):
    _name = 'hr.payslip.attendance'
    _description = 'Payslip Attendance'

    payslip_id = fields.Many2one(
        'hr.payslip', string="Payslip", ondelete="cascade", required=True
    )
    attendance_id = fields.Many2one(
        'hr.attendance', string="Attendance Record", required=True
    )
    report_id = fields.Many2one(
        'hr.payslip.report', string="Payslip Report", ondelete="cascade"
    )
    check_in = fields.Datetime(string="Check In", related="attendance_id.check_in", readonly=True)
    check_out = fields.Datetime(string="Check Out", related="attendance_id.check_out", readonly=True)
    worked_hours = fields.Float(string="Worked Hours", related="attendance_id.worked_hours", readonly=True)
    approved = fields.Boolean(string="Approved", default=False)
    employee_id = fields.Many2one('hr.employee', string="Employee", related="attendance_id.employee_id", store=True)

    def toggle_approval(self):
        """
        Toggle trạng thái phê duyệt của bản ghi attendance trong payslip.
        Khi bản ghi được phê duyệt ở một payslip, các payslip khác sẽ không thể chỉnh sửa bản ghi đó.
        """
        for record in self:
            # Đảo trạng thái `approved`
            record.approved = not record.approved
            _logger.info(f"Payslip {record.payslip_id.id}: Attendance ID {record.attendance_id.id} approval toggled to {record.approved}")

            # Tìm tất cả các payslip khác có sử dụng bản ghi attendance này
            other_payslip_lines = self.env['hr.payslip.attendance'].search([
                ('attendance_id', '=', record.attendance_id.id),
                ('payslip_id', '!=', record.payslip_id.id),
            ])

            if record.approved:
                # Làm mờ và vô hiệu hóa các bản ghi attendance trong các payslip khác
                other_payslip_lines.write({'approved': True})
            else:
                # Nếu bản ghi bị unapprove, các payslip khác có thể chỉnh sửa lại
                other_payslip_lines.write({'approved': False})

            # Tính toán lại tổng số giờ làm việc của payslip hiện tại
            record.payslip_id._compute_total_worked_hours()



    def action_view_details(self):
        """
        Mở popup hiển thị chi tiết timesheet liên quan đến attendance đã chọn.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Attendance and Timesheet Details',
            'view_mode': 'form',
            'res_model': 'attendance.timesheet.details',
            'target': 'new',
            'context': {
                'default_employee_id': self.attendance_id.employee_id.id,
                'default_date': self.attendance_id.check_in.date(),
                'default_check_in': self.attendance_id.check_in,
                'default_check_out': self.attendance_id.check_out,
                'default_worked_hours': self.attendance_id.worked_hours,
            },
        }


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'
    pass
