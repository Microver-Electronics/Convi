procedure Body;
begin
  ResetParameters;
  AddStringParameter('Action', 'All');
  RunProcess('PCB:Zoom');
  L('zoomed');
end;
